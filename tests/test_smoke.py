"""冒烟测试 —— 证明工程骨架本身是通的(不测业务逻辑)。

随着 M1–M11 推进,在 tests/ 下为每个迭代加对应测试。
比如 M3 你应该写一个测试:验证两个只读工具确实"同时"开始
(加 asyncio.sleep + 时间戳,断言总耗时 ≈ 单个而非两个之和)。
"""

import orchestra


def test_package_imports():
    """包能导入、版本号在。"""
    assert orchestra.__version__


def test_cli_runs(capsys):
    """CLI 入口能跑、能打印进度(骨架自带,现在就该绿)。"""
    from orchestra.cli import main

    main()
    out = capsys.readouterr().out
    assert "Agent Orchestra" in out
    assert "下一步" in out
