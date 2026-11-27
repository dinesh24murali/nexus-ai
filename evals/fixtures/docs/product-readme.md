# Lumen Pulse CLI

Lumen Pulse is a command-line tool that exports analytics reports from Lumen Labs.

## Install

Run `pip install lumen-pulse`. It requires Python 3.12 or newer.

## Configuration

Set the environment variable `LUMEN_API_KEY`. The default region is eu-west; override it with `--region`. A config file can be placed at `~/.pulse/config.toml`.

## Usage

Example: `pulse export --report weekly --format csv`. Supported export formats are csv, json and parquet.

## Limits

The API allows 100 requests per minute per key. Exceeding the limit returns HTTP 429. A single export is limited to 1 million rows.

## Troubleshooting

Error E102 means the API key is invalid. Error E207 means the report is still being generated; retry after 60 seconds.

## License

MIT.
