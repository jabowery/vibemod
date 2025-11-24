# Test Suite for modify_code_refactored.py

Comprehensive pytest test suite for the refactored modification system.

## Installation

```bash
pip install pytest
```

## Running Tests

### Run all tests
```bash
pytest test_modify_code.py -v
```

### Run specific test classes
```bash
# Test Phase 1 (extraction)
pytest test_modify_code.py::TestExtractCommandBlocks -v

# Test Phase 2 (canonicalization)
pytest test_modify_code.py::TestCanonicalizeCommand -v

# Test Phase 3 (execution)
pytest test_modify_code.py::TestExecuteCanonical -v

# Integration tests
pytest test_modify_code.py::TestIntegration -v
```

### Run specific tests
```bash
pytest test_modify_code.py::TestExtractCommandBlocks::test_multiple_commands -v
```

### Run with coverage
```bash
pip install pytest-cov
pytest test_modify_code.py --cov=modify_code_refactored --cov-report=html
```

## Test Organization

### Phase 1 Tests: Extract Command Blocks
- `TestNormalizeLLMQuirks`: Tests LLM output normalization
- `TestExtractCommandBlocks`: Tests command block extraction from raw text

### Phase 2 Tests: Canonicalize Commands  
- `TestParseBool`: Tests boolean string parsing
- `TestCanonicalizeCommand`: Tests transformation to canonical form

### Phase 3 Tests: Execute Canonical Commands
- `TestExecuteCanonical`: Tests execution of all command types
  - File operations (create, move, remove)
  - Directory operations
  - Python code modifications (declare, remove, update_header)
  - AST-based transformations

### Integration Tests
- `TestIntegration`: End-to-end tests of complete workflows

### Error Handling Tests
- `TestErrorHandling`: Edge cases and error conditions

## Test Coverage Summary

The test suite covers:

✅ **Command Block Extraction**
- Normal and malformed MMM headers
- Multiple commands
- Section separators
- Escaped separators
- Empty sections

✅ **Canonicalization**
- All 10 command types
- Argument validation
- Arity checking
- Default values
- Boolean parsing

✅ **Execution**
- File creation/replacement
- Directory creation/removal
- File moving
- Python module header updates
- Function/class declaration
- Nested declarations (methods in classes)
- Import handling
- AST transformations

✅ **Integration**
- Multi-command specifications
- Complex workflows
- Directory structure creation

✅ **Error Handling**
- Invalid syntax
- Missing files
- Malformed input
- Wrong arity

## Test Statistics

- **Total Tests**: 60+
- **Test Classes**: 7
- **Lines of Test Code**: ~650
- **Estimated Runtime**: < 5 seconds

## Continuous Integration

Example GitHub Actions workflow:

```yaml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Set up Python
        uses: actions/setup-python@v2
        with:
          python-version: '3.10'
      - name: Install dependencies
        run: |
          pip install pytest pytest-cov
      - name: Run tests
        run: |
          pytest test_modify_code.py -v --cov=modify_code_refactored
```

## Adding New Tests

When adding new functionality:

1. **Phase 1 changes**: Add tests to `TestExtractCommandBlocks`
2. **Phase 2 changes**: Add tests to `TestCanonicalizeCommand`
3. **Phase 3 changes**: Add tests to `TestExecuteCanonical`
4. **New commands**: Add tests to all three phases
5. **Integration**: Add end-to-end test to `TestIntegration`

Example test template:

```python
def test_new_feature(self, temp_dir):
    """Test description."""
    # Arrange
    input_data = "..."
    
    # Act
    result = function_under_test(input_data)
    
    # Assert
    assert result == expected_value
```

## Debugging Failed Tests

### Verbose output
```bash
pytest test_modify_code.py -vv
```

### Show print statements
```bash
pytest test_modify_code.py -s
```

### Run only failed tests from last run
```bash
pytest test_modify_code.py --lf
```

### Drop into debugger on failure
```bash
pytest test_modify_code.py --pdb
```

## Notes

- All file-system tests use temporary directories (auto-cleanup)
- Tests are isolated and can run in any order
- No external dependencies beyond pytest
- Git operations in `apply_modspec` are not tested (require git repo setup)
