# Contributing to unipiSync

Thanks for your interest in contributing to unipiSync!

## How to Contribute

### Reporting Bugs

If you find a bug, please open an issue with:
- A clear description of the problem
- Steps to reproduce the issue
- Expected vs actual behavior
- Your environment (Python version, OS, Unifi/Pi-hole versions)
- Relevant logs (use `--dry-run -v` for verbose output)

### Suggesting Features

Feature requests are welcome! Please open an issue describing:
- The problem you're trying to solve
- Your proposed solution
- Any alternatives you've considered

### Submitting Pull Requests

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/your-feature`)
3. Make your changes
4. Test your changes thoroughly
5. Commit with clear, descriptive messages
6. Push to your fork
7. Open a pull request

### Code Guidelines

- Follow existing code style (PEP 8 for Python)
- Add comments for complex logic
- Update documentation if needed
- Test with both `--dry-run` and actual sync

### Testing

Before submitting a PR, please test:
- Dry-run mode works correctly
- Actual sync creates/updates records properly
- Subnet filtering works as expected
- Error handling works (bad credentials, network issues, etc.)

## Questions?

Feel free to open an issue for any questions about contributing.
