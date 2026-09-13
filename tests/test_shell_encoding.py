import shutil

import pytest

from mini_claude_code.tools.shell import decode_shell_output, run_powershell


def test_decode_utf8_gbk_and_invalid_bytes():
    assert decode_shell_output("中文🙂\r\n".encode()) == "中文🙂\n"
    assert decode_shell_output("旧程序中文".encode("gbk")) == "旧程序中文"
    assert "输出编码警告" in decode_shell_output(b"A\xffB")


@pytest.mark.skipif(not shutil.which("powershell"), reason="Requires Windows PowerShell")
def test_real_powershell_unicode_stdout_and_stderr():
    output = run_powershell(
        "[Console]::Out.WriteLine('中文🙂'); [Console]::Error.WriteLine('错误🚀')"
    )
    assert "中文🙂" in output and "错误🚀" in output


@pytest.mark.skipif(not shutil.which("powershell"), reason="Requires Windows PowerShell")
def test_native_invalid_bytes_do_not_disappear():
    output = run_powershell(
        "$raw=[byte[]](65,255,66); [Console]::OpenStandardOutput().Write($raw,0,$raw.Length)"
    )
    assert "输出编码警告" in output and "A\ufffdB" in output
