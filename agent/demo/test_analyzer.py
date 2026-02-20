import os
import pytest
from agent.demo.log_analyzer import analyze_log

def test_analyze_log_basic(tmp_path):
    # 创建一个临时日志文件
    log_file = tmp_path / "test.log"
    content = """2023-10-27 10:00:00 INFO: Application started
2023-10-27 10:01:00 ERROR: Connection failed
2023-10-27 10:02:00 WARNING: Retrying...
2023-10-27 10:03:00 ERROR: Database unreachable
"""
    log_file.write_text(content, encoding='utf-8')
    
    stats, errors = analyze_log(str(log_file))
    
    assert stats['INFO'] == 1
    assert stats['ERROR'] == 2
    assert stats['WARNING'] == 1
    assert len(errors) == 2
    assert "Connection failed" in errors[0]
    assert "Database unreachable" in errors[1]

def test_analyze_log_no_timestamp(tmp_path):
    # 测试没有时间戳的情况
    log_file = tmp_path / "no_ts.log"
    content = """INFO: Simple info message
ERROR: Critical error message
"""
    log_file.write_text(content, encoding='utf-8')
    
    stats, errors = analyze_log(str(log_file))
    
    assert stats['INFO'] == 1
    assert stats['ERROR'] == 1
    assert len(errors) == 1

def test_analyze_log_empty_file(tmp_path):
    # 测试空文件
    log_file = tmp_path / "empty.log"
    log_file.write_text("", encoding='utf-8')
    
    stats, errors = analyze_log(str(log_file))
    
    assert len(stats) == 0
    assert len(errors) == 0

def test_analyze_log_invalid_format(tmp_path):
    # 测试格式不匹配的行
    log_file = tmp_path / "invalid.log"
    content = """This is an invalid line
2023-10-27 10:00:00 INFO: Valid line
"""
    log_file.write_text(content, encoding='utf-8')
    
    stats, errors = analyze_log(str(log_file))
    
    # 只有匹配的行才会被统计
    assert stats['INFO'] == 1
    assert len(stats) == 1
