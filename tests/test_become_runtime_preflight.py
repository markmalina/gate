from types import SimpleNamespace
import unittest

from app.become.runtime_preflight import LIVE_FLAGS, MODULES, build_runtime_preflight


def _importer(module_name: str) -> SimpleNamespace:
    symbol = dict(MODULES)[module_name]
    return SimpleNamespace(**{symbol: object()})


class RuntimePreflightTests(unittest.TestCase):
    def test_preflight_proves_all_modules_without_effects(self) -> None:
        receipt = build_runtime_preflight(importer=_importer, environ={})
        self.assertEqual(receipt["module_count"], len(MODULES))
        self.assertTrue(all(item["importable"] for item in receipt["modules"]))
        self.assertIs(receipt["provider_execution_enabled"], False)
        self.assertEqual(receipt["external_effects"], 0)
        self.assertIs(receipt["production_ready"], False)

    def test_every_live_effect_flag_fails_closed(self) -> None:
        for flag in LIVE_FLAGS:
            with self.subTest(flag=flag), self.assertRaisesRegex(
                RuntimeError, "live_flags_must_remain_disabled"
            ):
                build_runtime_preflight(importer=_importer, environ={flag: "true"})

    def test_missing_runtime_symbol_fails_closed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "runtime_symbol_missing"):
            build_runtime_preflight(importer=lambda _name: SimpleNamespace(), environ={})


if __name__ == "__main__":
    unittest.main()
