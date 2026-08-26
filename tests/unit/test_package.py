"""Baseline checks for project discovery and test configuration."""


def test_application_package_is_importable() -> None:
    import app

    assert app.__doc__ == "Autonomous coding agent application package."
