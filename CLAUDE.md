# Working in this repository

Home Assistant add-ons for one house. The owner's main configuration lives in a
separate, private repository you cannot see; what you need from it is written
into each brief. This repository is **public**: never commit addresses,
credentials, people's names or data exported from the house.

## Branch first

- Never commit to `main`. Before the first change, branch from an up-to-date
  `main`: `git fetch origin && git switch -c <addon>/<topic> origin/main`, for
  example `zigbee_watch/first-version`.
- Push the branch and open a pull request if you can. The owner reviews and
  merges; never merge yourself.
- At the start of every session, `git fetch origin` and rebase your branch on
  `origin/main`, because reviews and change requests arrive on `main`.

## Where things are

Each piece of work has a folder `work/<addon>/`:

| File | Written by | What |
|---|---|---|
| `BRIEF.md` | the owner | the task, and the context about the house, Home Assistant and the network |
| `change-requests/` | the owner | reviews of your work and change requests, numbered `NN-<topic>.md`, indexed in its `README.md`; each has a **Status** line |
| `NOTES.md` | you | your working notes, on your branch |

- **Read before starting:** `BRIEF.md`, then every open item in
  `change-requests/` on `origin/main`. Work open requests in number order.
- **When you finish a request,** set its Status line to `done <version>` in the
  same commit, on your branch.
- **`NOTES.md`** holds what the owner needs to review: status, the choices you
  made and why, open questions, and anything you could not verify. Keep it to
  the current state; history is in git. Put questions there rather than
  guessing - but do not stop for them when a sensible default exists; state the
  default and carry on.

## How add-ons are built here

- Python, pure-Python dependencies pinned exactly in `requirements.txt`
  (including transitive ones); `build.yaml` pins the base image per
  architecture. Current pin: `ghcr.io/home-assistant/{aarch64,amd64}-base-python:3.13-alpine3.24-2026.10.3`.
  `aarch64` is production, `amd64` is for development; nothing else.
- MQTT through the Supervisor's injected `mqtt` service (`services: [mqtt:need]`);
  those credentials work on the house broker.
- On **every** MQTT connect: subscribe again, publish availability `online`,
  republish discovery and state (the broker may be restarted with a clean
  session). Also republish when `homeassistant/status` says `online`.
- Entities via MQTT discovery under `homeassistant/`, with an availability
  topic carrying a last will. Names in Norwegian (bokmål); ids fixed with
  `default_entity_id` so they never change with wording.
- An input that cannot be trusted is published as unavailable, not as its last
  value.
- Tests run without the house: a real MQTT broker in the test process (`amqtt`)
  and fakes for everything else. Every "Done when" item in a brief is a test.
- Each release: version bump in `config.yaml`, `CHANGELOG.md`, the add-on's
  `README.md`/`DOCS.md`.
- `mirror_bridge` is the closest existing example of these conventions.
