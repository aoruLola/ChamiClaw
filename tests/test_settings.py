import os
import tempfile
import unittest
from pathlib import Path

from chamiclaw.settings import load_settings


class SettingsLoadTest(unittest.TestCase):
    def setUp(self):
        self._old_env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._old_env)

    def test_load_yaml_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "database:",
                        "  path: data/from-yaml.db",
                        "execution:",
                        "  dry_run: true",
                        "evaluate:",
                        "  paper_horizons_min: [5, 30, 1440]",
                    ]
                ),
                encoding="utf-8",
            )
            settings = load_settings(str(config_path), dotenv_path=str(Path(tmp) / ".env"))

        self.assertEqual(settings.db_path, "data/from-yaml.db")
        self.assertIs(settings.raw["execution"]["dry_run"], True)
        self.assertEqual(settings.raw["evaluate"]["paper_horizons_min"], [5, 30, 1440])

    def test_json_compat_and_env_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config_path.write_text(
                '{"database":{"path":"data/from-json.db"},"execution":{"dry_run":true}}',
                encoding="utf-8",
            )
            os.environ["CHAMICLAW_DB_PATH"] = "data/from-env.db"
            os.environ["CHAMICLAW_DRY_RUN"] = "false"
            settings = load_settings(str(config_path), dotenv_path=str(Path(tmp) / ".env"))

        self.assertEqual(settings.db_path, "data/from-env.db")
        self.assertIs(settings.raw["execution"]["dry_run"], False)

    def test_dotenv_loaded_but_runtime_env_has_priority(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            config_path.write_text("database:\n  path: data/default.db\n", encoding="utf-8")
            dotenv_path = Path(tmp) / ".env"
            dotenv_path.write_text("CHAMICLAW_DB_PATH=data/from-dotenv.db\n", encoding="utf-8")

            os.environ["CHAMICLAW_DB_PATH"] = "data/from-runtime-env.db"
            settings = load_settings(str(config_path), dotenv_path=str(dotenv_path))

        self.assertEqual(settings.db_path, "data/from-runtime-env.db")


if __name__ == "__main__":
    unittest.main()
