"""rulecheck: a tidy may not lose any hard fact of the old rules."""
from ai_sync import rulecheck

BEFORE = """<!-- owner-preferences:start -->
### Rules added by agents
- 2026-10-12 (claude-code, ai-sync): Every commit carries `Agent: <tool>`; run `aisync log --project x` after work. Reason: owner asked
- 2026-10-18 (claude-code, ops-jobs): Files live in ~/projects/ops-jobs/HANDOFF.md, checked at 06:45, retry 2 times (E098, T-20261019-09). Reason: x
- 2026-10-20 (codex, tps): Old KPI blank means unknown (KPI-AMD-004). Reason: y
<!-- owner-preferences:end -->
"""

TIDY = """<!-- owner-preferences:start -->
## 协作与派单
- Every commit carries `Agent: <tool>`; after work `aisync log --project x`. (10/12)
- Files in ~/projects/ops-jobs/HANDOFF.md, checked 06:45, retry 2 times. (10/18 · E098 · T-20261019-09)
## 已取代
- KPI blank means unknown (KPI-AMD-004)，被「空着按 0 计」取代。
<!-- owner-preferences:end -->
"""


def test_facts_pick_paths_commands_ids_times():
    f = rulecheck.facts("see ~/a/b.py, run `aisync log`, at 06:45, E098, T-20261019-09, KPI-AMD-004, 3 days")
    for want in ("~/a/b.py", "aisync log", "06:45", "E098", "T-20261019-09", "KPI-AMD-004", "3 days"):
        assert want in f, want


def test_identity_passes():
    assert rulecheck.check(BEFORE, BEFORE) == []


def test_tidy_with_superseded_passes():
    assert rulecheck.check(BEFORE, TIDY) == []


def test_lost_fact_fails():
    lost = TIDY.replace("06:45", "early morning")
    assert [m["fact"] for m in rulecheck.check(BEFORE, lost)] == ["06:45"]


def test_superseded_entry_must_name_replacement():
    bad = TIDY.replace("，被「空着按 0 计」取代", "")
    assert [m["fact"] for m in rulecheck.check(BEFORE, bad)] == ["KPI-AMD-004"]


def test_cli_exit_codes(tmp_path):
    b, a = tmp_path / "b.md", tmp_path / "a.md"
    b.write_text(BEFORE, encoding="utf-8")
    a.write_text(TIDY, encoding="utf-8")
    assert rulecheck.run(str(b), str(a)) == 0
    a.write_text(TIDY.replace("E098", "E0"), encoding="utf-8")
    assert rulecheck.run(str(b), str(a)) == 1


def test_header_and_reason_are_not_facts_but_names_are():
    r = "2026-10-12 08:18 CDT (aider, ai-sync): run kpi_sync and notion-mirror-pull at 06:35. Reason: owner said 23:59 E001"
    f = rulecheck.facts(r)
    assert {"kpi_sync", "notion-mirror-pull", "06:35"} <= f
    assert "08:18" not in f and "23:59" not in f and "E001" not in f


def test_slash_in_prose_is_not_a_path():
    f = rulecheck.facts("SDKs/APIs and 按E044/E051删除旧文本, but see ~/a/b and /etc/x/y and ~/.zshrc")
    assert "/APIs" not in f and not any("删除" in x for x in f)
    assert {"~/a/b", "/etc/x/y", "~/.zshrc"} <= f


def test_pointers_to_other_rules_are_not_facts():
    from ai_sync.rulecheck import facts
    f = facts("2026-10-15 09:38 CDT (claude-code, x): CORRECTION to the 2026-10-15 09:04 github1 naming rule: "
              "use top-<topic>. Serena follow-up to the 2026-10-12 16:09 rule: run `aisync news`. "
              "Supersedes the 2026-10-23 11:44 borrow-first rule. Reason: why")
    assert "09:04" not in f and "16:09" not in f and "11:44" not in f
    assert "follow-up" not in f and "borrow-first" not in f
    assert "aisync news" in f


def test_the_time_rule_pointer_is_not_a_fact():
    from ai_sync.rulecheck import facts
    assert "02:25" not in facts("x, exactly as the 02:25 (claude-code, team-inbox) rule says. Reason: y")


def test_aisync_prose_is_not_a_subcommand():
    f = rulecheck.facts("mistakes from the aisync launch become rules; run `aisync rule add` and aisync claims now")
    assert not any(x.startswith("aisync launch") for x in f)
    assert "aisync claims now" in f


# ---------------------------------------------------- non-vacuous (11/02) ----
def _files(tmp_path, before, after, archive=None):
    b, a = tmp_path / "b.md", tmp_path / "preferences.md"
    b.write_text(before, encoding="utf-8")
    a.write_text(after, encoding="utf-8")
    if archive is not None:
        (tmp_path / "rules-archive.md").write_text(archive, encoding="utf-8")
    return str(b), str(a)


def test_no_retired_entries_anywhere_is_vacuous(tmp_path, capsys):
    """The 11/02 bug: retired rules moved out of the block; the old checker read 0 of them and passed."""
    b, a = _files(tmp_path, BEFORE, BEFORE)          # identity, no 已取代 section, no archive
    assert rulecheck.run(b, a) == 1
    out = capsys.readouterr().out
    assert "VACUOUS" in out and "retired entries" in out


def test_normal_tidy_passes_and_prints_counts(tmp_path, capsys):
    b, a = _files(tmp_path, BEFORE, TIDY)
    assert rulecheck.run(b, a) == 0
    assert "checked: old rules 3, current rules 2, retired entries 1" in capsys.readouterr().out
    assert (tmp_path / "state" / "checker_counts.json").exists()       # state beside the hub, not in the repo


def test_archive_counts_as_retired_entries(tmp_path):
    archive = "# archive\n- [R-001] KPI blank means unknown (KPI-AMD-004) · retired: x · replaced_by: y 被「空着按 0 计」取代\n"
    after = TIDY.replace("## 已取代\n- KPI blank means unknown (KPI-AMD-004)，被「空着按 0 计」取代。\n", "")
    b, a = _files(tmp_path, BEFORE, after, archive)
    assert rulecheck.counted(BEFORE, after, archive)["retired entries"] == 1
    assert rulecheck.run(b, a) == 0


def test_drop_over_half_fails_without_flag(tmp_path, capsys):
    many = BEFORE.replace("<!-- owner-preferences:end -->",
                          "\n".join(f"- rule {i} with no facts at all" for i in range(10)) + "\n<!-- owner-preferences:end -->")
    tidy_many = TIDY.replace("## 已取代", "\n".join(f"- rule {i} with no facts at all" for i in range(10)) + "\n## 已取代")
    b, a = _files(tmp_path, many, tidy_many)
    assert rulecheck.run(b, a) == 0
    b, a = _files(tmp_path, BEFORE, TIDY)             # 13 -> 3 old rules, 12 -> 2 current rules
    assert rulecheck.run(b, a) == 1
    assert "more than half fewer" in capsys.readouterr().out
    assert rulecheck.run(b, a, accept_drop="tidy merged the test rules") == 0
