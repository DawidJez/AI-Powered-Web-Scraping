"""
The model proposes, the HTML decides. These are the checks that stand between an invented
selector and a browser click, plus the repairs that rebuild what the model got wrong.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from orchestrator import Orchestrator


def orchestrator():
    """the real __init__ builds four clients and wants API keys - none of this needs them"""
    instance = object.__new__(Orchestrator)
    instance.debug = False
    return instance


GRID = """
<div class="ag-header-row">
  <div class="ag-header-cell" aria-colindex="1"><span class="ag-header-cell-text">Product number</span></div>
</div>
<div class="ag-header-row">
  <div class="ag-header-cell" aria-colindex="1"><button ref="eButtonShowMainFilter">f</button></div>
</div>
<input id="ag-61-input" disabled="" aria-label="Product number Filter Input">
<select id="ohmic"><option value="4">4</option></select>
<select id="many" multiple><option value="9">9</option></select>
"""

FACETS = """
<div><h3>Component Type</h3>
  <input id="facet-general.componentType-Adapter"><input id="facet-general.componentType-Plug"></div>
<div><h3>Type</h3>
  <input id="facet-general.type-N/A"><input id="facet-general.type-Backshell"></div>
"""


class LastCompound(unittest.TestCase):
    """a plain split() breaks on a space inside an attribute value"""

    def test_attribute_with_a_space_survives(self):
        selector = '.ag-header-cell button[aria-label="Open Filter Menu"]'
        self.assertEqual(orchestrator()._last_compound(selector), 'button[aria-label="Open Filter Menu"]')

    def test_single_part_selector(self):
        self.assertEqual(orchestrator()._last_compound("#facet-x"), "#facet-x")

    def test_empty_selector(self):
        self.assertIsNone(orchestrator()._last_compound(""))


class SelectorsInScript(unittest.TestCase):
    """one place answers which elements a script touches, in the order it touches them"""

    def test_order_is_kept_and_ids_become_selectors(self):
        script = ("(function(){ const box = document.querySelector('.panel'); "
                  "const el = document.getElementById('facet-x'); })();")
        self.assertEqual(orchestrator()._selectors_in_script(script), [".panel", "#facet-x"])

    def test_no_script_gives_no_targets(self):
        self.assertEqual(orchestrator()._selectors_in_script(None), [])


class DisabledControls(unittest.TestCase):
    """grids ship one filter input per column and leave them disabled until the menu opens"""

    def test_disabled_input_is_recognised(self):
        self.assertTrue(orchestrator()._is_disabled_in_html(GRID, "#ag-61-input"))

    def test_enabled_control_is_not(self):
        self.assertFalse(orchestrator()._is_disabled_in_html(GRID, '[ref="eButtonShowMainFilter"]'))

    def test_script_targeting_a_disabled_control(self):
        script = "(function(){ document.getElementById('ag-61-input').value = 'x'; })();"
        self.assertTrue(orchestrator()._script_targets_disabled(GRID, script, "Product number"))

    def test_bare_tag_name_is_not_used_to_judge(self):
        """'input' asks about every input on the page and says nothing about the one meant"""
        every_input_disabled = "<div><input disabled=''><input disabled=''></div>"
        script = "(function(){ document.querySelector('input').value = 'x'; })();"
        self.assertFalse(orchestrator()._script_targets_disabled(every_input_disabled, script, "x"))


class ClosedSelectOption(unittest.TestCase):
    """an <option> of a plain dropdown is not natively clickable"""

    def test_option_of_a_plain_select(self):
        self.assertTrue(orchestrator()._is_closed_select_option(GRID, "#ohmic option[value='4']"))

    def test_option_of_a_multiple_select_is_fine(self):
        self.assertFalse(orchestrator()._is_closed_select_option(GRID, "#many option[value='9']"))


class DistinctiveValues(unittest.TestCase):
    """a bare number matches half the page and crowds out the windows that matter"""

    def test_part_number_is_distinctive(self):
        self.assertTrue(orchestrator()._is_distinctive_value("TLMG1100"))

    def test_bare_number_is_not(self):
        self.assertFalse(orchestrator()._is_distinctive_value("6"))

    def test_number_with_a_unit_is_not(self):
        self.assertFalse(orchestrator()._is_distinctive_value("3.0A"))


class RepairGateSelector(unittest.TestCase):
    """the header and its filter button sit in different rows, so scoping by the header hits nothing"""

    def test_selector_is_rebuilt_around_the_column_attribute(self):
        analisys = {
            "gate_script": ("(function(){ const h = [...document.querySelectorAll('.ag-header-cell')]"
                            ".find(e => e.textContent.includes('Product number')); "
                            "h.querySelector('[ref=\"eButtonShowMainFilter\"]').click(); })();"),
            "gate_selector": ".ag-header-cell:has(.x) [ref='eButtonShowMainFilter']",
        }
        repaired = orchestrator()._repair_gate_selector(analisys, GRID, "Product number")
        self.assertIsNotNone(repaired)
        self.assertIn('aria-colindex="1"', repaired)


class RepairFacetChoice(unittest.TestCase):
    """a filter called Type also matches the Component Type panel - the heading settles it"""

    def test_options_from_the_wrong_panel_are_rebuilt(self):
        analisys = {"available_options": [{"value": "Adapter", "script": "x", "selector": None}]}
        orchestrator()._repair_facet_choice(analisys, FACETS, "Type", ["N/A"])
        self.assertEqual([o["value"] for o in analisys["available_options"]], ["N/A"])
        self.assertIn("facet-general.type-N/A", analisys["available_options"][0]["script"])

    def test_a_panel_that_already_has_the_value_is_left_alone(self):
        analisys = {"available_options": [{"value": "Adapter", "script": "x", "selector": None}]}
        before = list(analisys["available_options"])
        orchestrator()._repair_facet_choice(analisys, FACETS, "Component Type", ["Adapter"])
        self.assertEqual(analisys["available_options"], before)

    def test_unknown_filter_name_changes_nothing(self):
        analisys = {"available_options": [{"value": "Adapter", "script": "x", "selector": None}]}
        before = list(analisys["available_options"])
        orchestrator()._repair_facet_choice(analisys, FACETS, "No Such Field", ["whatever"])
        self.assertEqual(analisys["available_options"], before)


class CompleteMissingOptions(unittest.TestCase):
    """long option lists come back truncated - a requested value is rebuilt from its twins"""

    def test_missing_value_is_rebuilt_from_the_id_family(self):
        analisys = {"available_options": [{
            "value": "Backshell",
            "script": "(function() { document.getElementById('facet-general.type-Backshell').click(); })();",
            "selector": None,
        }]}
        orchestrator()._complete_missing_options(analisys, FACETS, ["N/A"], "Type")
        self.assertIn("N/A", [o["value"] for o in analisys["available_options"]])


class DedupeRecords(unittest.TestCase):
    """the identifier often comes back as a link, and a dict cannot go into a set"""

    def test_link_shaped_identifier_does_not_crash_and_dedupes(self):
        link = {"text": "TMP117", "href": "http://x"}
        out = orchestrator()._dedupe_records([{"MPN": link}, {"MPN": link}, {"MPN": "TMP75"}])
        self.assertEqual(len(out), 2)

    def test_records_without_the_key_are_kept_apart(self):
        out = orchestrator()._dedupe_records([{"a": 1}, {"a": 2}])
        self.assertEqual(len(out), 2)

    def test_plain_duplicates_collapse(self):
        out = orchestrator()._dedupe_records([{"MPN": "TMP75"}, {"MPN": "TMP75"}])
        self.assertEqual(len(out), 1)


TYPING = ('(function() { const input = document.querySelector("ti-search-field")'
          '.shadowRoot.querySelector("input"); setter.call(input, %s); })();')


class RestoreValuePlaceholder(unittest.TestCase):
    """The model bakes the first value into the typing script instead of leaving __VALUE__."""

    def test_a_correct_script_is_left_alone(self):
        script = TYPING % '"__VALUE__"'
        self.assertEqual(orchestrator()._restore_value_placeholder(script, ["TMP75"]), script)

    def test_baked_value_becomes_the_placeholder(self):
        out = orchestrator()._restore_value_placeholder(TYPING % '"TMP75"', ["TMP75", "TMP117"])
        self.assertEqual(out, TYPING % '"__VALUE__"')

    def test_single_quotes_work_too(self):
        out = orchestrator()._restore_value_placeholder(TYPING % "'TMP75'", ["TMP75"])
        self.assertEqual(out, TYPING % "'__VALUE__'")

    def test_the_longer_value_wins_over_its_own_prefix(self):
        out = orchestrator()._restore_value_placeholder(TYPING % '"TMP117"', ["TMP1", "TMP117"])
        self.assertEqual(out, TYPING % '"__VALUE__"')

    def test_an_unknown_value_is_left_as_written(self):
        script = TYPING % '"something else"'
        self.assertEqual(orchestrator()._restore_value_placeholder(script, ["TMP75"]), script)

    def test_no_script_stays_none(self):
        self.assertIsNone(orchestrator()._restore_value_placeholder(None, ["TMP75"]))


class FakeExtractor:
    """extract() is an API call - hand the guard canned pages instead."""

    def __init__(self, pages):
        self.pages = list(pages)
        self.tokens_usage = []

    def extract(self, page_data, user_prompt, available_fields):
        return self.pages.pop(0)


def page(total, records):
    return {"total_records_declared": total, "records": records}


def run_extract(pages, values, filters_applied=True, unfiltered_total=397):
    instance = orchestrator()
    instance.extractor = FakeExtractor(pages)
    instance.unread_pages = 0
    instance.unfiltered_pages = 0

    filtered_data = {
        "filtered_data": [{"identifier_value": v} for v in values],
        "resolved_scripts": [{"name": "Product number"}] if filters_applied else [],
    }

    return instance, instance.extract_data(
        "https://www.ti.com/sensors", filtered_data, "MPN", unfiltered_total=unfiltered_total
    )


class NoEffectFilterGuard(unittest.TestCase):
    """One value that does not filter must not take down the values that did."""

    def test_one_bad_page_is_dropped_and_the_rest_survive(self):
        instance, out = run_extract(
            [page(6, [{"MPN": "TMP75"}]), page(397, [{"MPN": "x"}]), page(4, [{"MPN": "TMP117"}])],
            ["TMP75", "ISO", "TMP117"],
        )
        self.assertEqual(out, [[{"MPN": "TMP75"}], [{"MPN": "TMP117"}]])
        self.assertEqual(instance.unfiltered_pages, 1)

    def test_every_page_unfiltered_still_stops_the_run(self):
        with self.assertRaises(ValueError):
            run_extract([page(397, [{"MPN": "x"}]), page(397, [{"MPN": "y"}])], ["ISO", "TMP"])

    def test_the_guard_is_off_when_no_filter_was_applied(self):
        instance, out = run_extract([page(397, [{"MPN": "x"}])], ["ISO"], filters_applied=False)
        self.assertEqual(out, [[{"MPN": "x"}]])
        self.assertEqual(instance.unfiltered_pages, 0)


if __name__ == "__main__":
    unittest.main()
