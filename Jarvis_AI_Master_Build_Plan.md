# Jarvis AI (Telegram) — Master Build Plan

## What this is
A single, practical blueprint for building a Telegram-based personal AI assistant with modular agents, using a simple backend-first architecture that can be expanded in MVPs.

This document is written to be used with a coding harness agent like Codex, Claude Code, Cursor, or any similar builder.

---

## 1) Product vision

Build a **Telegram Jarvis** that can handle daily tasks through chat:

- todos
- reminders
- memory
- expenses
- research
- YouTube topic/script planning
- future integrations

The assistant should feel like one smart system, but internally it should be split into focused agents.

---

## 2) Core product decisions

### What we are building
- A **Telegram bot** as the main UI
- A **single FastAPI backend**
- A **multi-agent architecture**
- A **replaceable LLM layer**
- A **modular monolith** first

### What we are NOT building in MVPs
- no image generation
- no video generation
- no voice generation
- no file storage system for media
- no mobile app
- no web dashboard
- no microservices
- no Kubernetes
- no Kafka / RabbitMQ
- no vector DB in the first version
- no complex cloud storage setup

### Important principle
Keep the system simple enough that one person can build, debug, and deploy it.

---

## 3) Recommended stack

### Frontend / UI
- Telegram Bot API

### Backend
- Python 3.13
- FastAPI
- Uvicorn

### Agent orchestration
- LangGraph for workflows and multi-step agent logic
- LangChain only when a helper integration is useful
- LiteLLM as the provider abstraction layer

### LLM providers
Primary:
- Google Gemini

Fallback / alternatives:
- Groq
- OpenRouter

Future optional:
- GPT
- Claude API
- Local Ollama models

### Database
- PostgreSQL on Supabase

Store:
- users
- chats
- todos
- reminders
- memory items
- expenses
- agent logs
- task history

### Cache / sessions / lightweight state
- Redis on Upstash

Use for:
- session data
- temporary context
- rate limit state
- queue-like lightweight jobs
- short-lived agent state

### Scheduling
- APScheduler for reminders and recurring jobs

### Hosting / deployment
- Docker
- GitHub Actions
- Google Cloud Run

### Monitoring
- Cloud Run logs
- later add Sentry if needed

---

## 4) Architecture summary

```text
Telegram
   ↓
FastAPI Backend
   ↓
Jarvis Core
   ├── Agent Registry
   ├── Planner
   ├── LLM Manager
   ├── Memory Manager
   ├── Tool Registry
   └── Scheduler
         ↓
      LangGraph
         ↓
   Specialized Agents
         ↓
PostgreSQL + Redis
```

### What each layer does

#### Telegram
User interface only.  
All commands, chats, and responses happen here.

#### FastAPI backend
This is the **brain** of the system:
- receives Telegram updates
- authenticates user
- decides what to do
- calls agents
- stores state
- returns responses

#### Planner
Reads the user message and decides:
- which agent to use
- whether one agent is enough
- whether multiple agents are needed
- whether a tool call or direct reply is enough

#### Agent Registry
A list of all agents available in the system.

#### LLM Manager
A wrapper around different model providers so the backend does not depend on one API.

#### Tool Registry
All external actions the assistant can perform:
- add todo
- create reminder
- save memory
- write expense
- search web
- summarize text
- generate YouTube ideas
- etc.

#### Memory Manager
Stores useful long-term facts and short-term context.

#### Scheduler
Handles reminders and recurring jobs.

#### LangGraph
Used for workflows with multiple steps, branching, retries, and structured state.

---

## 5) LLM design

### Important rule
The LLM is **not** the whole brain.

The backend is the brain.  
The LLM is a reasoning component.

### Best design
Use a provider abstraction so models can be swapped.

Example:
- Gemini for general chat
- Groq for fast inexpensive calls
- OpenRouter for backup or experimentation

### Why this matters
- avoids vendor lock-in
- keeps costs low
- lets you switch models later without rewriting the app

### Suggested implementation idea
Create a `LLMProvider` interface and plug in:
- GeminiProvider
- GroqProvider
- OpenRouterProvider

---

## 6) Initial MVP agent list

Do not build everything at once. Start with a small but useful core.

### MVP 1 agents
1. **Orchestrator Agent**
   - routes the request
   - chooses the right agent
   - coordinates multi-step tasks

2. **Todo Agent**
   - add task
   - list tasks
   - complete task
   - delete task
   - priority / due date support later

3. **Reminder Agent**
   - one-time reminders
   - recurring reminders
   - daily schedule alerts

4. **Memory Agent**
   - save user preferences
   - store useful facts
   - recall past items on demand

### MVP 2
5. **Expense Agent**
   - add expense
   - categorize expense
   - monthly summary
   - simple budget insight

### MVP 3
6. **Research Agent**
   - search
   - summarize
   - compare
   - explain

### MVP 4
7. **YouTube Content Agent**
   - topic ideas
   - trend research
   - competitor scan
   - script generation
   - titles
   - hooks
   - thumbnails text ideas
   - descriptions
   - tags
   - SEO suggestions

### MVP 5
8. **Coding Agent**
   - code generation
   - bug fixing
   - code explanation
   - project scaffolding

### MVP 6
9. **Automation Agent**
   - recurring workflows
   - morning brief
   - combined multi-agent jobs

### Later
10. Integrations agent
11. Business / RestoFlow agent
12. Calendar agent
13. Email agent
14. Drive agent
15. GitHub agent

---

## 7) MVP roadmap

### MVP 1 — Personal productivity core
Goal: make it useful every day.

Features:
- add todo
- show todo
- complete todo
- add reminder
- show schedule
- remember something
- recall memory
- simple daily summary

Why this first:
- immediate utility
- easiest to test
- validates the whole stack

---

### MVP 2 — Expense tracker
Features:
- add expense quickly
- category detection
- monthly summary
- spending trends
- simple budget alerts

Examples:
- “Spent 250 on lunch”
- “How much did I spend this month?”

---

### MVP 3 — Research assistant
Features:
- web search
- topic summarization
- comparison answers
- research notes

Examples:
- compare two products
- summarize a long article
- explain a concept simply

---

### MVP 4 — YouTube content assistant
This is **not** for generating videos.

It only helps with:
- topic research
- idea generation
- script generation
- title generation
- hook writing
- description writing
- tags
- content calendar
- SEO suggestions

Not included:
- editing
- uploading
- voice
- thumbnails generation as images
- full automation of video creation

---

### MVP 5 — Coding assistant
Features:
- generate code
- explain code
- fix bugs
- create small project structures
- help with scripts and APIs

---

### MVP 6 — Automation
Features:
- scheduled reports
- morning brief
- recurring check-ins
- multi-step workflows

Example:
- every morning send calendar + tasks + reminders summary

---

### MVP 7 — Integrations
Optional future integrations:
- Google Calendar
- Gmail
- GitHub
- Notion
- Google Drive

---

### MVP 8 — Business layer
For your own projects later:
- RestoFlow analytics
- inventory insight
- sales summary
- operational reports

---

## 8) Commands the bot should understand

Start with simple command-like messages and natural language.

Examples:
- add buy milk
- remind me tomorrow at 8
- show my tasks
- remember my locker key is in bag
- I spent 350 on lunch
- research best budget phones
- give me YouTube ideas for AI channel
- write script for topic 3
- explain this code
- summarize this article

---

## 9) Database plan

### Core tables
- users
- conversations
- messages
- todos
- reminders
- memory_items
- expenses
- agent_runs
- scheduled_jobs

### Notes
Keep the schema simple at first.

Examples:
- `users` linked to Telegram user ID
- `todos` linked to user
- `reminders` linked to user
- `memory_items` linked to user
- `expenses` linked to user and optional category

---

## 10) Memory strategy

Use two types of memory:

### Short-term memory
Stored in Redis:
- current conversation context
- temporary state
- workflow execution state

### Long-term memory
Stored in PostgreSQL:
- user preferences
- repeated facts
- personal notes
- important project details

Keep memory explicit:
- store only when useful
- retrieve when relevant
- avoid storing too much noise

---

## 11) Tool strategy

Every action the assistant performs should be a tool.

Examples:
- create_todo()
- list_todos()
- complete_todo()
- create_reminder()
- add_expense()
- save_memory()
- search_web()
- generate_youtube_ideas()
- write_script_outline()
- summarize_text()

This makes the assistant easier to test and extend.

---

## 12) Agent interface design

Each agent should follow a consistent interface.

Recommended structure:

- `name`
- `description`
- `can_handle(request, context)`
- `execute(request, context)`
- `required_tools()`

This lets the orchestrator register and call any agent in a standard way.

---

## 13) Suggested folder structure

```text
app/
  main.py
  api/
    telegram.py
    health.py
  core/
    config.py
    logging.py
    types.py
  planner/
    router.py
    prompts.py
  llm/
    manager.py
    providers/
      gemini.py
      groq.py
      openrouter.py
  agents/
    base.py
    orchestrator.py
    todo.py
    reminder.py
    memory.py
    expense.py
    research.py
    youtube.py
    coding.py
    automation.py
  tools/
    todo_tools.py
    reminder_tools.py
    memory_tools.py
    expense_tools.py
    research_tools.py
    youtube_tools.py
  services/
    telegram_service.py
    llm_service.py
    memory_service.py
    scheduler_service.py
  database/
    session.py
    models.py
    migrations/
  workers/
    jobs.py
  utils/
    time.py
    text.py
tests/
docker/
```

---

## 14) Hosting plan

### Recommended hosting stack
- **Backend**: Google Cloud Run
- **Database**: Supabase Postgres
- **Cache**: Upstash Redis
- **CI/CD**: GitHub Actions
- **Container**: Docker
- **Code**: GitHub

### Why this works
- cheap to start
- easy to deploy
- easy to scale later
- no need for Kubernetes in MVP

### Do we need file storage?
No, not for this version.

Telegram itself already handles the user-facing file exchange for normal bot usage, and you are not building media generation features in MVPs.

---

## 15) Free / low-cost build approach

For a low-budget build:
- Gemini as primary LLM
- Groq as fallback
- OpenRouter as backup
- Supabase free tier
- Upstash free tier
- Cloud Run free tier if usage is light

This keeps the project near-zero cost while you build and test.

---

## 16) Build order

### Step 1
Set up repository, project structure, config, and basic health check.

### Step 2
Connect Telegram bot to FastAPI webhook or polling.

### Step 3
Create basic user/session storage.

### Step 4
Implement LLM manager.

### Step 5
Implement agent base class and orchestrator.

### Step 6
Build MVP 1 agents:
- todo
- reminder
- memory

### Step 7
Add scheduler support.

### Step 8
Add tests.

### Step 9
Deploy to Cloud Run.

### Step 10
Only after stable MVP 1, add expense agent.

---

## 17) Prompting and agent behavior rules

### System rules
- be concise
- ask only necessary follow-ups
- prefer actions over long explanations
- use tools when needed
- keep output structured
- do not hallucinate stored facts
- confirm when a task is ambiguous
- handle dates carefully

### Memory rules
- save only useful facts
- do not store everything
- prefer explicit user-approved memory items

### Research rules
- summarize clearly
- cite sources internally in the app if you choose to store them
- never assume freshness without checking

---

## 18) YouTube Content Agent detail

This agent is only for planning and writing, not video production.

### Inputs
- channel niche
- target audience
- video goal
- keyword/topic
- competitor examples
- desired length
- tone

### Outputs
- topic list
- titles
- hooks
- script outline
- full script
- intro
- outro
- CTA
- description
- tags
- chapter ideas
- thumbnail text ideas

### Example flow
User:
“I want AI startup video ideas”

Agent:
- suggests topics
- ranks them
- gives angle for each
- writes one script when asked
- writes title + hook + description

---

## 19) What makes this project good

- small enough to start
- useful enough to actually use
- flexible enough to grow
- cheap enough for early development
- modular enough for future agents
- clean enough to hand off to a coding harness

---

## 20) What the coding harness should do with this file

When using Codex / Claude Code / similar:
1. read this spec fully
2. create project scaffolding
3. implement MVP 1 only
4. do not jump ahead
5. write tests for each module
6. keep code modular
7. make every agent a separate file
8. keep provider logic isolated
9. keep secrets in environment variables
10. prepare for future agents without hardcoding them

---

## 21) MVP 1 definition of done

MVP 1 is done when:
- Telegram bot receives messages
- FastAPI backend responds
- planner routes correctly
- todo agent works
- reminder agent works
- memory agent works
- data is persisted
- scheduler works
- deployment works on Cloud Run

---

## 22) Final direction

Build it as a **modular monolith**.

Do not over-engineer.

Do not start with too many agents.

Make one useful assistant first, then expand one MVP at a time.

The final product should feel like:
- one chat
- many skills
- stable memory
- reliable task execution
- easy expansion

---

## 23) Suggested next file to create
If you want the next step after this master plan, create:

- `ARCHITECTURE.md` for full system design
- `DATABASE_SCHEMA.md` for tables and relations
- `AGENTS_SPEC.md` for every agent contract
- `API_SPEC.md` for endpoints and webhook routes
- `DEPLOYMENT.md` for Cloud Run setup
- `PROMPTS.md` for agent prompts and routing prompts

That would make the code generation process much easier.
