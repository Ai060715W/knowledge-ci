import unittest

from src.cli import apply, analyze, ask_owner, check_llm, discover, feedback, generate, init, inject, migrate
from src.cli.main import COMMANDS, build_parser


class KcCliTest(unittest.TestCase):
    def test_all_commands_registered(self):
        self.assertEqual(
            set(COMMANDS),
            {
                "init",
                "analyze",
                "discover",
                "ask-owner",
                "generate",
                "apply",
                "inject",
                "feedback",
                "check-llm",
                "migrate",
            },
        )

    def test_each_command_has_help_and_run(self):
        for name, module in COMMANDS.items():
            with self.subTest(command=name):
                self.assertTrue(module.HELP)
                self.assertTrue(callable(module.run))
                self.assertTrue(callable(module.build_parser))

    def test_subcommand_parsing_binds_handler(self):
        parser = build_parser()
        args = parser.parse_args(["inject", "--file", "src/x.py"])
        self.assertEqual(args.command, "inject")
        self.assertEqual(args.file, "src/x.py")
        self.assertTrue(callable(args._run))

    def test_generate_subcommand_parses_legacy_flags(self):
        parser = build_parser()
        args = parser.parse_args(
            ["generate", "--commit", "abc1234", "--unit", "u1", "--mock-response", "[]"]
        )
        self.assertEqual(args.commit, "abc1234")
        self.assertEqual(args.unit, "u1")
        self.assertEqual(args.mock_response, "[]")

    def test_migrate_subcommand_parses_flags(self):
        parser = build_parser()
        args = parser.parse_args(["migrate", "--registry", "r.json", "--dry-run"])
        self.assertTrue(args.dry_run)
        self.assertEqual(args.registry, "r.json")

    def test_unknown_command_rejected(self):
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["frobnicate"])


class ModuleInterfaceTest(unittest.TestCase):
    def test_module_surface(self):
        for module in (
            init,
            analyze,
            discover,
            ask_owner,
            generate,
            apply,
            inject,
            feedback,
            check_llm,
            migrate,
        ):
            with self.subTest(module=module.__name__):
                self.assertTrue(module.HELP)
                self.assertTrue(callable(module.main))
                self.assertTrue(callable(module.run))
                self.assertTrue(callable(module.build_parser))


if __name__ == "__main__":
    unittest.main()
