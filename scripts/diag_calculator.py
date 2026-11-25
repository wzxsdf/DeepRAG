from src.tools import calculate  # ponytail: agents/tools.py已删(评审P2死代码), 工具真身统一在src.tools

def main():
    # 正确性
    assert "= 7" in calculate("1+2*3"), calculate("1+2*3")
    assert "= 1024" in calculate("2**10"), calculate("2**10")
    assert "4" in calculate("sqrt(16)"), calculate("sqrt(16)")
    assert "= 1.5" in calculate("3/2"), calculate("3/2")
    # 安全边界: 注入表达式必须被拒(返回错误提示, 不执行)
    for inj in ["__import__('os').system('ls')", "open('x')", "os.system('ls')", "1+1;import os"]:
        r = calculate(inj)
        assert "无法计算" in r or "语法错误" in r, f"注入未被拒: {inj} -> {r}"
    # 边界: 除零/空/超长
    assert "除零" in calculate("1/0")
    assert "为空" in calculate("")
    print("calculator ok")

if __name__ == "__main__":
    main()
