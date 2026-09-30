"""
The model writes the JavaScript, this code rewrites it. These are the rewrites - every one of them
exists because a real run failed without it, so a change here should have to break a test first.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from firecrawl_client import FirecrawlClient


def client():
    """the real __init__ wants an API key, and none of these methods touch the network"""
    instance = object.__new__(FirecrawlClient)
    instance.debug = False
    return instance


class NativeSetter(unittest.TestCase):
    """React and Vue ignore a plain assignment, so it is rewritten into the prototype's setter"""

    def test_plain_assignment_is_rewritten(self):
        out = client()._use_native_setter("el.value = \"TMP75\";")
        self.assertIn("getOwnPropertyDescriptor", out)
        self.assertIn('"TMP75"', out)

    def test_assignment_to_a_call_result_is_rewritten(self):
        script = "root.shadowRoot.querySelector('input').value = 'abc';"
        self.assertNotEqual(client()._use_native_setter(script), script)

    def test_comparison_is_left_alone(self):
        script = "if (el.value == 'x') { return 1; }"
        self.assertEqual(client()._use_native_setter(script), script)


class RealSubmit(unittest.TestCase):
    """dispatchEvent('submit') runs the handlers but never submits the form"""

    def test_dispatched_submit_becomes_request_submit(self):
        out = client()._real_submit("form.dispatchEvent(new Event('submit', { bubbles: true }));")
        self.assertIn("requestSubmit", out)

    def test_unrelated_event_is_left_alone(self):
        script = "el.dispatchEvent(new Event('change', { bubbles: true }));"
        self.assertEqual(client()._real_submit(script), script)


class ConfirmWithEnter(unittest.TestCase):
    """a synthetic input event is not a keystroke, and some components only listen for keys"""

    def test_typing_without_confirmation_gets_enter(self):
        out = client()._confirm_with_enter("el.dispatchEvent(new Event('input', { bubbles: true }));")
        self.assertIn("KeyboardEvent", out)
        self.assertIn("Enter", out)

    def test_script_that_already_sends_keys_is_left_alone(self):
        script = ("el.dispatchEvent(new Event('input', { bubbles: true })); "
                  "el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));")
        self.assertEqual(client()._confirm_with_enter(script), script)

    def test_script_that_submits_is_left_alone(self):
        script = "el.dispatchEvent(new Event('input', {})); form.requestSubmit();"
        self.assertEqual(client()._confirm_with_enter(script), script)


class ConfirmWithButton(unittest.TestCase):
    """
    A field in a shadow root is confirmed by the component's own button, which the fetched HTML
    never shows. Narrow on purpose: it may only touch a script that types and confirms nothing.
    """

    SHADOW = ("(function(){ const i = document.querySelector('ti-search-field[placeholder=\"x\"]')"
              ".shadowRoot.querySelector('input'); i.dispatchEvent(new Event('input', {bubbles:true})); })();")

    def test_shadow_root_typing_gets_a_button_click(self):
        self.assertNotEqual(client()._confirm_with_button(self.SHADOW), self.SHADOW)

    def test_script_with_its_own_click_is_left_alone(self):
        script = self.SHADOW.replace("})();", "btn.click(); })();")
        self.assertEqual(client()._confirm_with_button(script), script)

    def test_script_without_shadow_root_is_left_alone(self):
        script = ("(function(){ const i = document.querySelector('th.parts-search input'); "
                  "i.dispatchEvent(new Event('input', {bubbles:true})); })();")
        self.assertEqual(client()._confirm_with_button(script), script)

    def test_script_that_types_nothing_is_left_alone(self):
        script = "(function(){ document.getElementById('facet-x').click(); })();"
        self.assertEqual(client()._confirm_with_button(script), script)

    def test_clear_and_close_controls_are_excluded(self):
        out = client()._confirm_with_button(self.SHADOW)
        self.assertIn("clear", out)
        self.assertIn("close", out)


class ScopedApply(unittest.TestCase):
    """Apply buttons come in twins - one per filter panel plus a page-level one"""

    def test_the_last_selector_before_the_click_is_the_one_used(self):
        script = ("(function(){ const panel = document.querySelector('.facets'); "
                  "panel.querySelector('.btn-apply').click(); })();")
        self.assertIn('".btn-apply"', client()._scoped_apply_script(script))

    def test_script_without_a_selector_is_left_alone(self):
        script = "(function(){ someHandle.click(); })();"
        self.assertEqual(client()._scoped_apply_script(script), script)


class AdvancingNext(unittest.TestCase):
    """a next-page control picked by a page number only ever works once"""

    def test_numeric_attribute_is_resolved_at_click_time(self):
        script = "(function(){ document.querySelector('button[data-page=\"1\"]').click(); })();"
        out = client()._advancing_next_script(script)
        self.assertIn('const attr = "data-page"', out)
        self.assertIn("data-current", out)

    def test_control_named_by_its_label_is_left_alone(self):
        script = "(function(){ document.querySelector('button[aria-label=\"Next Page\"]').click(); })();"
        self.assertEqual(client()._advancing_next_script(script), script)

    def test_the_attribute_closest_to_the_click_wins(self):
        script = ("(function(){ const box = document.querySelector('[data-region=\"2\"]'); "
                  "box.querySelector('button[data-page=\"1\"]').click(); })();")
        self.assertIn('const attr = "data-page"', client()._advancing_next_script(script))


class ValueSubstitution(unittest.TestCase):
    """the placeholder reaches the script in whichever quotes the model felt like using"""

    def test_both_quote_styles_are_replaced(self):
        for script in ('el.value = "__VALUE__";', "el.value = '__VALUE__';", "el.value = __VALUE__;"):
            out = FirecrawlClient.VALUE_PLACEHOLDER.sub(lambda _: '"TMP75"', script)
            self.assertNotIn("__VALUE__", out, script)


if __name__ == "__main__":
    unittest.main()
