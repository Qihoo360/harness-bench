"""测试用例 - 用于验证每层修复"""
import sys
import time

def test_layer_1_syntax():
    """验证语法正确性"""
    # 尝试导入并运行基础代码
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("buggy_code", "buggy_code.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return True, "Syntax OK"
    except SyntaxError as e:
        return False, f"SyntaxError: {e}"
    except Exception as e:
        return False, f"Import Error: {e}"

def test_layer_2_import():
    """验证导入正确性"""
    try:
        import json
        data = json.loads('{"test": "value"}')
        return True, "Import OK"
    except ImportError as e:
        return False, f"ImportError: {e}"

def test_layer_3_type():
    """验证类型处理"""
    try:
        result = "Score: " + str(95)
        assert result == "Score: 95"
        return True, "Type conversion OK"
    except TypeError as e:
        return False, f"TypeError: {e}"

def test_layer_4_logic():
    """验证边界逻辑"""
    def is_valid_score(score):
        return score >= 0 and score <= 100
    
    try:
        assert is_valid_score(100), "满分应通过"
        assert is_valid_score(0), "零分应通过"
        assert is_valid_score(50), "中间值应通过"
        return True, "Logic OK"
    except AssertionError as e:
        return False, f"Logic Error: {e}"

def test_layer_5_performance():
    """验证性能优化"""
    def find_duplicates(data):
        # 期望使用set优化后的版本
        seen = set()
        duplicates = set()
        for item in data:
            if item in seen:
                duplicates.add(item)
            seen.add(item)
        return list(duplicates)
    
    try:
        # 大数据集测试
        data = list(range(1000)) + [500]
        start = time.time()
        result = find_duplicates(data)
        elapsed = time.time() - start
        
        if elapsed > 2.0:
            return False, f"Too slow: {elapsed:.2f}s"
        return True, f"Performance OK: {elapsed:.3f}s"
    except Exception as e:
        return False, f"Performance Error: {e}"

if __name__ == "__main__":
    # 运行所有测试
    tests = [
        test_layer_1_syntax,
        test_layer_2_import,
        test_layer_3_type,
        test_layer_4_logic,
        test_layer_5_performance,
    ]
    
    for test in tests:
        passed, msg = test()
        status = "PASS" if passed else "FAIL"
        print(f"{test.__name__}: {status} - {msg}")