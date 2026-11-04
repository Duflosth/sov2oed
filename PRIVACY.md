# Privacy

sov2oed collects no data and has no account, telemetry or analytics. Nothing is sent to its author.

- **The skill and the plugin** run where Claude runs code: in Claude's code-execution environment (claude.ai, the
  desktop app, Cowork) or on your computer (Claude Code). The code makes no network request. The schedules you
  give Claude are handled under the terms of your Claude account.
- **The command line and the MCP server** run on your computer and make no network request.
- **`sov2oed auto`** is the one exception: it sends the schedule's content to the AI provider you choose (Anthropic,
  or an OpenAI-compatible endpoint you name), under that provider's terms.

The outputs (OED files, report, annotated workbook, questions) are written to the folder you choose and nowhere
else.

Questions: open an issue at https://github.com/Duflosth/sov2oed/issues or write to thomas@aldarion.si.
