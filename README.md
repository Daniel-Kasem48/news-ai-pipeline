# Arabic News AI Pipeline

**Python · LLM (DeepSeek / OpenAI-compatible) · SQLite / MySQL · stdlib HTTP server · Docker**

Breaking news arrives as a flood of short posts from many channels – the same event reported five times, in different words, with channel boilerplate around it. This pipeline turns that stream into a clean list of **stories**: it cleans each post, uses an LLM to decide whether it is news and what it is about, and then decides how it relates to the stories already open – **new story, new development, update, confirmation from another source, or duplicate**.

It comes with an **evaluation harness**: a set of hand-labelled real Arabic posts, an "oracle" mode that tests all the plumbing without calling the model, and a report that scores every run (accuracy, merge/split errors, compression, token cost), so prompt changes are measured, not guessed.

## Use cases

- **Breaking-news ticker for a news site** – feed it the channels a newsroom follows; publish one item per story instead of five copies of the same event, updated as new developments arrive.
- **Newsroom monitoring dashboard** – "what happened in the last 60 minutes, and how many independent sources report it?" (`digest.py`, `/stories/latest`).
- **Alerts and digests** – push a story only once it is confirmed by 2+ channels, or send an hourly digest to subscribers.
- **Media monitoring** – for analysts, NGOs and research teams tracking events across many Arabic-language sources.
- **Fact-checking triage** – single-source stories vs. stories several channels corroborate within minutes.
- **Research datasets** – labelled, deduplicated event timelines built from raw post streams.
- **Other languages and domains** – the stages are generic (adapters, normalisation, gate, signature, decision); swap the normaliser, prompts and gazetteer for another language or for market, sports or crypto news.

## Expected costs

Measured on a real run of **623 posts**: about **2,900 input tokens per post (≈70% served from the provider's prompt cache)** and **~155 output tokens per post**. Stable prompt prefixes are ordered first so the cache hits.

At DeepSeek's list prices (check current rates before relying on them):

| Pricing | Cache hit / miss input, output (USD per 1M tokens) | Cost per 1,000 posts |
|---|---|---|
| DeepSeek V3.2 | $0.028 / $0.28, $0.42 | **≈ $0.37** |
| DeepSeek V3 (earlier pricing) | $0.07 / $0.27, $1.10 | ≈ $0.55 |

| Scenario | Posts per day | LLM cost per month |
|---|---|---|
| Small newsroom – 10 channels | ~1,000 | **≈ $11–17** |
| Regional monitor – 30 channels | ~3,000 | ≈ $33–50 |
| Large monitor – 100 channels | ~10,000 | ≈ $110–165 |

Hosting is small: the server is one Python process with SQLite (a ~$5–10/month VPS is enough), or MySQL if you already run one. Posts that are not news are dropped after the first call, so they never pay for the second one. Get exact numbers for your own run with `PRICE_IN=… PRICE_OUT=… PRICE_CACHED=… make report`.

## Pipeline

```
raw post
  │
  ▼  Stage 0 – adapters.py      per-channel cleanup: footers, credits, bilingual duplicates, mirrored-post prefixes
  ▼  normalize.py               Arabic normalisation: diacritics, letter variants, Arabic-Indic digits, invisible chars
  ▼  Stage 1+2 – LLM call A     gate (is this breaking news?) + signature: actor, action, location, aliases
  ▼  candidate retrieval        three channels: entity match · place hierarchy (places.py) · TF-IDF lexical overlap (lexical.py)
  ▼  Stage 3 – LLM call B       decision vs. open stories: duplicate | confirmation | update | new_development | new_story
  ▼  clock.py                   story lifetime: inactivity timeout + hard max age
  ▼  store.py / db.py           persisted in SQLite or MySQL, schema managed by migrations
  ▼  digest.py                  product view: stories in a time window, ranked by how many distinct channels reported them
```

Design choices worth noting:

- **Retrieval is measured separately from judgement.** `calibrate.py` replays the corpus and checks whether the right story was even in the shortlist – decision accuracy can never exceed retrieval recall.
- **Three retrieval channels**, because each fails differently: entities miss channels that never name the actor, locations miss posts without a place, so a corpus-weighted lexical score (IDF over the open stories) catches the rest.
- **Same source is never a confirmation** – confirmations must come from a different channel; that is the corroboration signal the digest ranks by.
- **No secrets or prices in code** – API keys from `.env`, token prices from environment variables at report time.

## Project structure

```
newsai/
├── adapters.py        Stage 0 – per-channel post assembly
├── normalize.py       Arabic text normalisation
├── llm.py             LLM client (OpenAI-compatible), token + cache accounting, dry-run
├── prompts/           call_a.txt (gate + signature), call_b.txt (decision)
├── places.py          place hierarchy + aliases (data/places.json)
├── lexical.py         TF-IDF candidate retrieval
├── clock.py           story expiry rules, timestamp parsing
├── run.py             the pipeline
├── report.py          scores a run against the labelled fixtures; compares two runs
├── calibrate.py       retrieval-recall sweep (no API calls)
├── digest.py          time-window story digest
├── server.py          HTTP control server: /health, /status, /runs, /runs/latest, /stories/latest
├── ingest_paste.py    turns pasted raw posts into a fixture skeleton for labelling
├── db.py              one code path for SQLite and MySQL (dialect differences in one place)
├── store.py           reads/writes; migrates on connect
├── migrate.py         migration runner with a ledger
└── migrations/        numbered .sql files, one per dialect
fixtures/posts.jsonl   36 hand-labelled posts with expected class and decision
tests/                 88 checks: adapters, clock, places, store, migrations, server
```

## Getting started

```bash
python -m venv .venv && . .venv/bin/activate
make install
cp .env.example .env          # add DEEPSEEK_API_KEY for live runs

make test                     # unit tests (SQLite)
make oracle                   # full pipeline, no API calls – should score 100%
make run NOTE="prompt v2"     # live run against the LLM
make report                   # score the latest run
python -m newsai.report <run_a> <run_b>   # compare two runs
```

Price a run by exporting the provider's current rates (USD per 1M tokens):

```bash
PRICE_IN=… PRICE_OUT=… PRICE_CACHED=… make report
```

### Oracle mode

`--oracle` replays the fixture expectations instead of calling the model. It tests the store, candidate lookup, decision handling and metrics – everything except the prompts – and should always score 100%. If it does not, the harness is broken, not the prompts. It already caught two real bugs: stories not recording their target (so an alias match was unreachable) and a merge/split metric scored at the wrong level.

### Storage

SQLite by default, MySQL 8.0.19+ when `DATABASE_URL=mysql://…` is set – same code either way; `db.py` holds every dialect difference. Schema changes are numbered migrations with a ledger table, applied automatically on connect, so code never runs against an unmigrated database.

```bash
TEST_MYSQL_URL=mysql://root:pw@127.0.0.1:3306/newsai python -m tests.test_store
```

### HTTP server and Docker

```bash
NEWSAI_API_KEY=change-me make serve          # or:
docker build -t newsai . && docker run -p 8080:8080 --env-file .env newsai
curl -H "X-API-Key: change-me" localhost:8080/status
curl -X POST -H "X-API-Key: change-me" localhost:8080/runs
```

The server runs the pipeline on an interval (`NEWSAI_INTERVAL_SECONDS`), allows only one run at a time (`409` if busy) and exposes the latest run and stories as JSON.

## Example report (oracle run)

```
posts 36 | dropped 5 | stories 18
   compression         1.72x
   developments        27
   merge errors        0   <- development holding 2+ true events
   split errors        0   <- true event spread over 2+ developments
```
