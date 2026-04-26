# cyberPublisher

**cyberPublisher** is a template-driven CLI publishing tool for generating flat-file output from structured data, validating results, and optionally shipping published output to a remote destination.

It is designed for practical publishing workflows where you want to render repeatable output from templates and data without dragging in a heavyweight CMS or database stack.

## What it does

- Publishes flat-file output from templates and input data
- Supports structured input such as JSON, XML, CSV, and Markdown
- Generates one file per record or aggregate/index-style output
- Validates local output and related publishing artifacts
- Supports optional remote deployment over SSH/SFTP
- Maintains instance-scoped policy and runtime state under `~/.pypub`

## Install

### From source (development / local)

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

### From built wheel

```bash
python -m pip install ./dist/cyberpublisher-1.0.2-py3-none-any.whl
```

## Command

```bash
cyberpublisher --help
```

## Basic usage

### Publish local output

```bash
cyberpublisher -t ./template.html -d ./data.json -o ./build/
```

### Publish and ship to a configured destination

```bash
cyberpublisher -t ./template.html -d ./data.json -o ./build/ --ship myserver
```

### Manage destinations

```bash
cyberpublisher dest list
cyberpublisher dest add myserver
cyberpublisher dest remove myserver
```

## Requirements

- Python 3.8+
- `cryptography`
- `jinja2`
- `paramiko`

## License

Licensed under the GNU General Public License v3.0.

See the `LICENSE` file for the full license text.
