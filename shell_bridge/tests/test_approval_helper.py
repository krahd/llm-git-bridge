import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('approval_helper_under_test', ROOT / 'approval_helper.py')
helper = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(helper)

class ApprovalHelperUITests(unittest.TestCase):
    def test_dialog_is_visible_and_requires_deliberate_allow(self):
        script = helper.SCRIPT
        for token in [
            'setActivationPolicy($.NSApplicationActivationPolicyRegular)',
            'app.finishLaunching()',
            'ChatGPT is asking to cross a safety boundary',
            'Technical details (exact request)',
            'No action is taken until you make a deliberate choice',
            'cancelButton.setKeyEquivalent(\"\\u001b\")',
            'allowButton.setKeyEquivalent(\"\\r\")',
            'NSEventModifierFlagCommand',
            'NSModalPanelWindowLevel',
            'setHidesOnDeactivate(false)',
            'NSWindowCollectionBehaviorCanJoinAllSpaces',
            'NSWindowCollectionBehaviorFullScreenAuxiliary',
            'makeKeyAndOrderFront(null)',
            'orderFrontRegardless()',
            'makeFirstResponder(textView)',
            '840,200',
        ]:
            self.assertIn(token, script)
        self.assertNotIn('Allow ChatGPT to perform this action?', script)

    def test_cancel_remains_fallback_for_every_non_allow_response(self):
        self.assertIn('response === $.NSAlertSecondButtonReturn ? \"allow\" : \"cancel\"', helper.SCRIPT)

if __name__ == '__main__':
    unittest.main()
