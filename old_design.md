# alfred_bot — Design

> Living design doc. Sections marked **✍️ YOURS** are deliberately left for you to fill — that's the exercise, not an oversight.

## Goal

For households, splitting bills is a massive pain: someone has to order, they will then typically have to pay, and then once the item arrives someone has to sit down and do accounting-tangental work in order to figure out who owes whom how much. If the reconciliation is not done the right away, orders stack up and it becomes increasingly difficult to recall who ordered what and keep record of orders.

Given this, the goal is to create a system that automatically ingests a recent `.eml` order confirmation from a grocery store (Tesco), parses and serialises the data contained to extract the items bought and their respective costs, and then lets the users conveniently claim items and decide which items should be split via a messenger interface (Telegram); the system will also keep a serialised record of the order history and allow to configure for additional settings like grocery subscriptions.

## Scope & non-goals

**In scope**
- Tesco order-confirmation `.eml` ingestion
- Parsing items + fees (pick / pack / deliver / basket charges) from the order email
- Telegram claim/split flow via emoji reactions
- Even splits only ($1/x$ across $x$ members)
- Serialised, queryable order history
- Subscription fee via config (Option B)

**Out of scope (explicitly)**
- Other grocery stores
- Other messengers
- Currencies other than GBP
- Multiple households per chat
- Uneven / weighted splits
- Substitutions, refunds, out-of-stock items
- Parsing the subscription confirmation email (we use config instead — see decisions)
- Manual cost entry (anti-tampering)

## Key decisions & rationale

**Ingestion**
- Emails are forwarded **manually** by the user to a dedicated address; a daemon runs **locally** on the user's machine (active only while the machine is on). No separate host (e.g. Raspberry Pi) for now.

**Parsing & SSOT**
- The order `.eml` is the **single source of truth** for that order's items and costs. Users may **not** input costs manually — prevents tampering.
- Only items that were actually ordered and affect the final cost are ingested (OOS items excluded).
- On parse failure: post a message to the chat flagging that a manual review is required.

**Reconciliation**
- Users claim/split items with emoji reactions, then run `/reconcile`.
- `/reconcile` is gated: it won't run until **every** item is claimed or marked split.
- All splits are even ($1/x$). Uneven splits out of scope.
- Payer is already determined (whoever placed the order), so there's no "who paid?" step.

**Subscription (Option B)**
- Subscription fee is **not** parsed from a separate email. It's declared in config and applied via date logic (first order on/after the billing day each month).
- Rationale: avoids writing/maintaining a second parser. Hybrid (config declares relationship, email supplies figure) is a possible later refinement — out of scope now.
- Trade-off accepted: config can drift from Tesco's real price; we assume members won't tamper and that tampering would be obvious.

**Currency / households**
- GBP only. Single household per chat.

## Configuration  ✍️ YOURS (you said you'd nail the concrete shape)

We've agreed config-as-code drives behaviour. Sketch the actual schema (yaml or json). It needs to express, at minimum:
- household members → telegram user id, display name, reaction emoji
- optional `default_payee`
- subscription block → enabled, monthly_fee, billing_day, attributed_to (member or "split")

<!-- Replace this with a concrete example config. -->

## Data model sketch  ✍️ YOURS

Plain bullet lists of entities → fields → relationships (no SQL, no JSON syntax). Questions to confront while you write it:
- Is a **claim** its own entity, or a field on an item? (Hint: can one item be claimed by multiple people when split?)
- How do you record that a subscription fee was **already applied** for a given month, so two orders in one month don't double-charge?
- What's the shape of the **history** record you want to query later (Q7: date, people involved, totals, paid_by)?
- JSON-on-disk vs SQLite — and why?

<!-- Your sketch goes here. -->

## Component diagram  ✍️ YOURS

One Mermaid `flowchart` of the pipeline, ~6–8 boxes, arrows for data flow.

```mermaid
flowchart LR
    %% Replace with your pipeline: email -> forward -> daemon -> parser -> store -> bot -> reactions -> reconcile
```

## Known unknowns  ✍️ YOURS

List 3–5 things you don't know yet but want on record. Honesty over completeness. One seeded example to show the format — add your own:

- **Telegram reaction permissions** — does the bot need admin in the group to receive `message_reaction` updates? (Verify against Bot API docs before building the reaction handler.)
<!-- Add yours below. -->

## Open threads

- Concrete config schema (see Configuration section).
- Haven't yet opened `data/example.eml` to inspect Tesco's actual HTML structure — that drives the parser design.
- Storage choice (JSON vs SQLite) pending the data model sketch.
