# Contributing Guidelines

## Testing Requirements

### Test Execution
- **Run all tests after completing any task**: Every change must be followed by running the full test suite
- **Command**: `pytest`
- **With coverage**: `pytest --cov=gitlab_watcher`
- **Target coverage**: Minimum 85% test coverage must be maintained

### Test Failure Handling
- **If tests fail**: Fix any broken tests immediately
- **Do not merge or commit** code that fails tests
- **Debug and resolve** issues before continuing with development
- **Run tests again** to ensure all tests pass

## Commit Guidelines

### Commit Message Format
- **Avoid conventional commit prefixes**: Do NOT use `feat:`, `fix:`, `chore:`, etc.
- **Use clear, descriptive commit messages** in English
- **Example**: "Add authentication handling for GitLab API"
- **Example**: "Fix state management race condition in processor"

### Commit Best Practices
- **Atomic commits**: Each commit should address a single logical change
- **Meaningful messages**: Describe what was changed and why
- **Reference issues**: Include issue numbers when applicable (e.g., "Closes #123")

## Development Workflow

### Code Quality
- **Follow Python best practices**: Use proper error handling, type hints, and docstrings
- **Maintain readability**: Code should be self-documenting with clear naming
- **Security first**: Validate inputs, avoid common vulnerabilities, use principle of least privilege

### Testing Strategy
- **Unit tests**: Test individual components in isolation
- **Integration tests**: Test interactions between components
- **Coverage**: Aim for 85%+ coverage for critical paths
- **Edge cases**: Consider and test edge cases and error conditions

## Project-Specific Guidelines

### Python Code Style
- **Use modern Python**: Follow PEP 8 style guidelines
- **Type hints**: Use type hints for function signatures and class methods
- **Error handling**: Use appropriate exception types and provide meaningful error messages
- **Logging**: Use structured logging with appropriate log levels

### Dependency Management
- **Requirements**: Keep dependencies minimal and well-maintained
- **Version pinning**: Pin specific versions for critical dependencies
- **Security**: Regularly update dependencies to address security vulnerabilities

## Troubleshooting

### Common Issues
- **Test failures**: Check for edge cases and error conditions
- **Import errors**: Verify Python path and virtual environment setup
- **Configuration issues**: Validate config file syntax and values
- **API timeouts**: Handle rate limiting and retry mechanisms

### Debug Tips
- **Use logging**: Add debug logging for complex operations
- **Isolate issues**: Reproduce issues in isolation when possible
- **Review changes**: Check recent commits for potential causes
- **Consult documentation**: Review project documentation and guidelines