import unittest

from ui.launch_config import load_launch_config


class LaunchConfigTests(unittest.TestCase):
    def test_defaults_keep_local_development_behavior(self):
        config = load_launch_config({})

        self.assertEqual(config.server_name, "127.0.0.1")
        self.assertEqual(config.server_port, 7860)
        self.assertIsNone(config.root_path)
        self.assertEqual(
            config.as_gradio_kwargs(),
            {
                "server_name": "127.0.0.1",
                "server_port": 7860,
                "share": False,
            },
        )

    def test_environment_overrides_are_converted_once(self):
        config = load_launch_config(
            {
                "GRADIO_SERVER_NAME": "0.0.0.0",
                "GRADIO_SERVER_PORT": "18080",
                "GRADIO_ROOT_PATH": "/learning",
            }
        )

        self.assertEqual(config.server_name, "0.0.0.0")
        self.assertEqual(config.server_port, 18080)
        self.assertEqual(config.root_path, "/learning")
        self.assertEqual(
            config.as_gradio_kwargs(),
            {
                "server_name": "0.0.0.0",
                "server_port": 18080,
                "root_path": "/learning",
                "share": False,
            },
        )

    def test_port_must_be_an_integer_in_range(self):
        for value in ("", "abc", "0", "65536"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "GRADIO_SERVER_PORT"):
                    load_launch_config({"GRADIO_SERVER_PORT": value})

    def test_root_path_must_be_absolute_when_present(self):
        with self.assertRaisesRegex(ValueError, "GRADIO_ROOT_PATH"):
            load_launch_config({"GRADIO_ROOT_PATH": "learning"})


if __name__ == "__main__":
    unittest.main()
