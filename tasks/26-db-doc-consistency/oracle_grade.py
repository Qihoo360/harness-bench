"""
Task 26: Database & Documentation Consistency Audit
Oracle grading logic for autonomous evaluation.
"""

import csv
import re
from pathlib import Path
from typing import Tuple, Dict, List


def _parse_sql_config(sql_file: Path) -> Dict[str, str]:
    """
    Extract config_key -> config_value mapping from backup.sql.
    Uses regex to parse INSERT statements reliably.
    """
    config = {}
    
    sql_content = sql_file.read_text()
    
    # Pattern to match: INSERT INTO `system_config` VALUES (1,'max_db_connections','500'), ...
    # We extract tuples of (id, config_key, config_value)
    insert_pattern = r"\(\d+,'([^']+)','([^']+)'\)"
    matches = re.findall(insert_pattern, sql_content)
    
    for key, value in matches:
        config[key] = value
    
    return config


def _parse_markdown_config(md_file: Path) -> Dict[str, str]:
    """
    Extract configuration parameters from deployment_guide.md.
    Maps descriptive names to config keys and values.
    """
    config = {}
    md_content = md_file.read_text()
    
    # Pattern: `(config_key)` with value **VALUE**
    # Examples: `max_db_connections` 为 **200**
    pattern = r'`([a-z_]+)`[^*]*\*\*(\d+)\*\*'
    matches = re.findall(pattern, md_content, re.IGNORECASE)
    
    for key, value in matches:
        config[key] = value
    
    # Additional patterns for natural language descriptions
    # "最大数据库连接数 (`max_db_connections`)" -> **200**
    pattern2 = r'`(config_[a-z_]+)`[^*]*\*\*([^*]+)\*\*'
    matches2 = re.findall(pattern2, md_content)
    for key, value in matches2:
        config[key] = value
    
    return config


def _identify_contradictions(
    db_config: Dict[str, str],
    doc_config: Dict[str, str]
) -> List[Tuple[str, str, str]]:
    """
    Find contradictions where both sources have the key but different values.
    Returns list of (config_key, db_value, doc_value) tuples.
    """
    contradictions = []
    
    # Only check keys that exist in both DB and doc
    common_keys = set(db_config.keys()) & set(doc_config.keys())
    
    for key in common_keys:
        db_val = db_config[key]
        doc_val = doc_config[key]
        
        if db_val != doc_val:
            contradictions.append((key, db_val, doc_val))
    
    # Sort by key for consistent output
    contradictions.sort(key=lambda x: x[0])
    
    return contradictions


def score_workspace(workspace_dir: str) -> dict:
    """
    Grade task 26 based on audit_report.csv accuracy.
    
    Three-dimensional scoring:
    1. SQL Extraction (30%): Can parse backup.sql correctly
    2. Markdown Extraction (30%): Can understand doc parameters
    3. Contradiction Accuracy (40%): Exact match on contradictions
    
    Validates:
    - Exactly 3 contradiction records (no header)
    - Correct keys: max_db_connections, api_rate_limit, worker_timeout
    - cache_ttl_seconds NOT in the report (critical!)
    - All values match expected DB->Doc mappings
    """
    workspace = Path(workspace_dir)
    
    # Paths to fixtures
    backup_sql = workspace / "fixtures" / "db" / "backup.sql"
    deployment_md = workspace / "fixtures" / "db" / "deployment_guide.md"
    audit_report = workspace / "audit_report.csv"
    
    results = {
        "sql_extraction": 0.0,
        "md_extraction": 0.0,
        "contradiction_accuracy": 0.0,
        "violations": [],
        "details": {},
        "row_scores": {}  # Per-row scoring
    }
    
    # Check if audit_report.csv exists
    if not audit_report.exists():
        results["violations"].append("audit_report.csv not found")
        results["score"] = 0.0
        results["rating"] = "fail"
        return results
    
    # Parse DB config
    try:
        db_config = _parse_sql_config(backup_sql)
        results["sql_extraction"] = 1.0
        results["details"]["db_config"] = db_config
    except Exception as e:
        results["violations"].append(f"Failed to parse backup.sql: {str(e)}")
        results["sql_extraction"] = 0.0
        results["score"] = 0.0
        results["rating"] = "fail"
        return results
    
    # Parse Markdown config
    try:
        doc_config = _parse_markdown_config(deployment_md)
        results["md_extraction"] = 1.0
        results["details"]["doc_config"] = doc_config
    except Exception as e:
        results["violations"].append(f"Failed to parse deployment_guide.md: {str(e)}")
        results["md_extraction"] = 0.0
    
    # Expected contradictions (what should be in the report)
    expected_contradictions = {
        "max_db_connections": ("500", "200"),
        "api_rate_limit": ("100", "500"),
        "worker_timeout": ("30", "120")
    }
    
    # Read and validate audit_report.csv
    try:
        rows = []
        with open(audit_report, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)
        
        contradiction_acc_score = 0.0
        report_keys = set()
        correct_rows = 0
        
        # Check row count
        if len(rows) != 3:
            results["violations"].append(
                f"Expected exactly 3 contradiction records, found {len(rows)}"
            )
        else:
            contradiction_acc_score += 0.2  # Row count correct: +20%
        
        # Validate each row
        for idx, row in enumerate(rows):
            key = row.get("Config_Key", "").strip()
            db_val = row.get("DB_Value", "").strip()
            doc_val = row.get("Doc_Value", "").strip()
            
            report_keys.add(key)
            row_score = 0.0
            row_errors = []
            
            # Check if key is valid
            if key not in expected_contradictions:
                row_errors.append(f"Unexpected key '{key}'")
            else:
                row_score += 0.25  # Key correct: +25%
                
                expected_db, expected_doc = expected_contradictions[key]
                
                # Check DB value
                if db_val == expected_db:
                    row_score += 0.375  # DB value exact: +37.5%
                else:
                    row_errors.append(
                        f"DB_Value mismatch: expected '{expected_db}', got '{db_val}'"
                    )
                
                # Check Doc value
                if doc_val == expected_doc:
                    row_score += 0.375  # Doc value exact: +37.5%
                else:
                    row_errors.append(
                        f"Doc_Value mismatch: expected '{expected_doc}', got '{doc_val}'"
                    )
            
            results["row_scores"][f"row_{idx}_{key}"] = {
                "score": row_score,
                "errors": row_errors
            }
            
            if row_score >= 1.0:
                correct_rows += 1
        
        # Award credit for correct rows (up to 60%)
        contradiction_acc_score += (correct_rows / 3.0) * 0.6
        
        # Check that all expected keys are present
        for expected_key in expected_contradictions:
            if expected_key not in report_keys:
                results["violations"].append(
                    f"Missing expected contradiction: {expected_key}"
                )
                contradiction_acc_score -= 0.2  # Penalty: -20%
        
        # CRITICAL: Verify cache_ttl_seconds is NOT in the report
        if "cache_ttl_seconds" in report_keys:
            results["violations"].append(
                "CRITICAL: cache_ttl_seconds should NOT be in report (it's consistent)"
            )
            contradiction_acc_score = 0.0  # Fatal error: entire accuracy score = 0
        
        # Clamp score to [0, 1]
        results["contradiction_accuracy"] = max(0.0, min(1.0, contradiction_acc_score))
        
        results["details"]["report_rows"] = len(rows)
        results["details"]["report_keys"] = list(report_keys)
        results["details"]["correct_rows"] = correct_rows
        
    except Exception as e:
        results["violations"].append(f"Failed to read audit_report.csv: {str(e)}")
        results["contradiction_accuracy"] = 0.0
    
    # ============ FINAL SCORE CALCULATION ============
    weights = {
        "sql_extraction": 0.30,
        "md_extraction": 0.30,
        "contradiction_accuracy": 0.40
    }
    
    final_score = (
        results["sql_extraction"] * weights["sql_extraction"] +
        results["md_extraction"] * weights["md_extraction"] +
        results["contradiction_accuracy"] * weights["contradiction_accuracy"]
    )
    
    results["score"] = final_score
    
    # Determine rating based on score
    if final_score >= 0.90:
        results["rating"] = "excellent"
    elif final_score >= 0.75:
        results["rating"] = "good"
    elif final_score >= 0.60:
        results["rating"] = "pass"
    else:
        results["rating"] = "fail"
    
    # Build explanation
    explanation_parts = [
        f"SQL extraction: {results['sql_extraction']:.1%}",
        f"MD extraction: {results['md_extraction']:.1%}",
        f"Contradiction accuracy: {results['contradiction_accuracy']:.1%}"
    ]
    
    results["explanation"] = " | ".join(explanation_parts)
    
    return results
