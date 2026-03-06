# Calculator Test Plan

## 1. Overview
This document outlines the test plan for the Calculator application. The goal is to ensure that all basic arithmetic operations, edge cases, and user interface elements function correctly.

## 2. Features to be Tested
- Addition (`+`)
- Subtraction (`-`)
- Multiplication (`*`)
- Division (`/`)
- Clear/Reset functionality
- Error handling (e.g., division by zero)

## 3. Test Cases

### 3.1 Basic Arithmetic
| Test Case ID | Description | Input | Expected Output |
|--------------|-------------|-------|-----------------|
| TC-01 | Test addition | 5 + 3 | 8 |
| TC-02 | Test subtraction | 10 - 4 | 6 |
| TC-03 | Test multiplication | 7 * 6 | 42 |
| TC-04 | Test division | 20 / 4 | 5 |

### 3.2 Edge Cases & Error Handling
| Test Case ID | Description | Input | Expected Output |
|--------------|-------------|-------|-----------------|
| TC-05 | Division by zero | 5 / 0 | Error/Undefined |
| TC-06 | Large number calculation | 999999 * 999999 | 999998000001 |
| TC-07 | Negative results | 3 - 8 | -5 |
| TC-08 | Decimal calculations | 5.5 + 2.1 | 7.6 |

## 4. Execution Plan
- Run automated unit tests for all mathematical operations.
- Perform manual UI testing to verify button responsiveness and display accuracy.
- Document and report any bugs found during the testing phase.
