# Contributing

Thank you for your interest in contributing! This project is maintained by [Kanjani AI Research](https://github.com/kanjani-ai-research).

## Development Setup

1. Clone the repository:
   ```bash
   git clone <repo-url>
   cd <project-dir>
   ```

2. Create a virtual environment and install in editable mode with dev dependencies:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -e '.[dev]'
   ```

## Running Tests

```bash
pytest tests/
```

For coverage:
```bash
pytest tests/ --cov --cov-report=term-missing
```

## Code Style

This project uses:
- **[Black](https://github.com/psf/black)** for code formatting
- **[Ruff](https://github.com/astral-sh/ruff)** for linting

Before submitting, ensure your code passes:
```bash
black .
ruff check .
```

## Submitting Pull Requests

1. Fork the repository
2. Create a feature branch from `main`:
   ```bash
   git checkout -b feature/your-feature
   ```
3. Make your changes, including tests
4. Ensure all tests pass and code style checks are clean
5. Commit with a clear, descriptive message
6. Push and open a Pull Request against `main`

### PR Guidelines

- Keep PRs focused — one feature or fix per PR
- Include tests for new functionality
- Update documentation if behavior changes
- Reference any related issues in the PR description

## Reporting Issues

Open an issue on GitHub with:
- A clear description of the problem
- Steps to reproduce
- Expected vs actual behavior
- Python version and OS

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).

## Organization

This project is part of the [Kanjani AI Research](https://github.com/kanjani-ai-research) organization.
