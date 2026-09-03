from hpupdates.infrastructure.windows.backend import CommandRunner, WindowsDriverBackend


class RecordingRunner(CommandRunner):
    def __init__(self, output: str) -> None:
        self.output = output
        self.commands: list[list[str]] = []

    def run(self, command: list[str]) -> str:
        self.commands.append(command)
        return self.output


def test_authenticode_accepts_only_valid_signature() -> None:
    valid = RecordingRunner("Valid\n")
    invalid = RecordingRunner("NotSigned\n")

    assert (
        WindowsDriverBackend(valid, platform="win32").verify_authenticode(r"C:\cache\driver.exe")
        is True
    )
    assert (
        WindowsDriverBackend(invalid, platform="win32").verify_authenticode(r"C:\cache\driver.exe")
        is False
    )


def test_create_restore_point_uses_checkpoint_computer() -> None:
    runner = RecordingRunner("")
    WindowsDriverBackend(runner, platform="win32").create_restore_point(
        "hp-driverctl before update"
    )
    assert "Checkpoint-Computer" in runner.commands[0][-1]
