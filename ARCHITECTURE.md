# RecoverAI - System Architecture

> **RecoverAI is an event-driven revenue recovery engine that continuously detects revenue at risk, estimates the incremental value of possible interventions, applies deterministic policy and freshness checks, executes one bounded action through an MCP-controlled Razorpay boundary, and re-evaluates from the resulting state until the revenue is recovered or recovery is safely stopped.**

---

## Core Architectural Principles

### One-Next-Action Decisioning
- Select exactly ONE permitted action based on current state
- After outcome, RE-EVALUATE before selecting another action
- **RecoverAI never commits to a pre-planned recovery sequence**
- Each subsequent action is selected from the latest observed state

### Separation of Concerns
- **LLM reasons** about what action might help
- **Economic engine measures** incremental value
- **Policy gate authorizes** deterministically
- **MCP boundary limits** capability
- **Freshness check re-validates** before execution

---

## High-Level Architecture

```mermaid
flowchart TB
    subgraph Events["Revenue Risk Events"]
        E1["payment.failed"]
        E2["checkout.abandoned"]
        E3["subscription.halted"]
        E4["invoice.overdue"]
    end

    subgraph Gateway["FastAPI Gateway"]
        GW["Verify + Persist + Queue"]
    end

    subgraph Queue["Redis Event Bus"]
        RQ["recovery-events<br/>execution-queue<br/>re-evaluate"]
    end

    subgraph Worker["Async Worker"]
        WK["Event Consumer<br/>Case Creator"]
    end

    subgraph Engine["RecoverAI Engine"]
        direction TB
        D["DIAGNOSE<br/>Classify failure type"]
        S["SCORE<br/>Economic evaluation"]
        P["POLICY<br/>Deterministic gate"]
        L["LLM DECIDE<br/>Groq analysis"]
        V["VALIDATE<br/>Hard validation"]
        
        D --> S --> P --> L --> V
    end

    subgraph MCP["MCP Execution Boundary"]
        direction TB
        FC["FRESHNESS CHECK<br/>• Case still open?<br/>• Already recovered?<br/>• Mandate valid?<br/>• Budget available?<br/>• Action still permitted?"]
        CC["CAPABILITY CHECK<br/>• Valid case ID<br/>• Supported action<br/>• Amount bounds<br/>• Required IDs present<br/>• Idempotency key"]
        EX["EXECUTOR<br/>Bounded tool call"]
        
        FC --> CC --> EX
    end

    subgraph Razorpay["Razorpay Test Mode"]
        API["create_order<br/>generate_payment_link<br/>retry_payment"]
    end

    subgraph Outcome["Outcome Processing"]
        WH["Webhook Receiver"]
        RE["RE-EVALUATE<br/>from fresh state"]
    end

    subgraph Terminal["Terminal States"]
        REC["RECOVERED"]
        STP["STOPPED"]
        ESC["ESCALATED"]
    end

    Events --> Gateway --> Queue --> Worker --> Engine
    Engine -->|"Authorized Action"| MCP
    MCP --> Razorpay
    Razorpay --> WH
    WH -->|"Success"| REC
    WH -->|"Failed"| RE
    RE -->|"Fresh State"| Engine
    Engine -->|"No Valid Action"| STP
    Engine -->|"Human Required"| ESC

    subgraph Persistence["Persistence & Observability"]
        PG[("PostgreSQL<br/>• webhook_events<br/>• recovery_cases<br/>• recovery_attempts<br/>• scheduled_actions")]
        LF["Langfuse Traces"]
    end

    Engine --> PG
    Engine --> LF
```

---

## Economic Decision Model

The core decision is **economic**, not just "LLM saw failure → send link".

```mermaid
flowchart TB
    subgraph Input["Case Context"]
        AMT["Amount at Risk<br/>₹18,500"]
        FC["Failure Code<br/>BAD_REQUEST_ERROR"]
        CTX["Customer Context<br/>Segment, History"]
    end

    subgraph P0["Natural Recovery"]
        P0C["P0 = P(recovery | no action)<br/>Example: 35%"]
    end

    subgraph Actions["Candidate Actions"]
        A1["RETRY<br/>Pa = 45%, Cost = ₹2"]
        A2["SEND_PAYMENT_LINK<br/>Pa = 55%, Cost = ₹5"]
        A3["OFFER_DISCOUNT<br/>Pa = 73%, Cost = ₹925"]
    end

    subgraph Uplift["Uplift Calculation"]
        U1["Uplift = Pa - P0"]
        U2["RETRY: 45% - 35% = +10pp"]
        U3["LINK: 55% - 35% = +20pp"]
        U4["DISCOUNT: 73% - 35% = +38pp"]
    end

    subgraph Net["Expected Incremental Net Recovery"]
        N1["= Uplift × Amount - Cost"]
        N2["RETRY: 10% × 18500 - 2 = ₹1,848"]
        N3["LINK: 20% × 18500 - 5 = ₹3,695"]
        N4["DISCOUNT: 38% × 18500 - 925 = ₹6,105"]
    end

    subgraph Decision["Best Permitted Action"]
        DEC["OFFER_DISCOUNT<br/>if permitted by policy"]
    end

    Input --> P0
    Input --> Actions
    P0 --> Uplift
    Actions --> Uplift
    Uplift --> Net
    Net --> Decision
```

---

## Recovery Case State Machine

```mermaid
stateDiagram-v2
    [*] --> CREATED: Event Received
    
    CREATED --> DIAGNOSED: diagnose_node
    DIAGNOSED --> SCORED: score_node
    SCORED --> POLICY_CHECK: policy_node
    
    state policy_decision <<choice>>
    POLICY_CHECK --> policy_decision
    
    policy_decision --> POLICY_APPROVED: Permitted action exists
    policy_decision --> no_action: No permitted action
    
    state no_action <<choice>>
    no_action --> ESCALATED: Human intervention possible
    no_action --> STOPPED: No valid recovery path
    
    POLICY_APPROVED --> EXECUTING: Immediate execution
    POLICY_APPROVED --> SCHEDULED: Deferred execution
    
    SCHEDULED --> FRESHNESS_CHECK: Timer fires
    
    state freshness_result <<choice>>
    FRESHNESS_CHECK --> freshness_result
    
    freshness_result --> CANCELLED: Already recovered
    freshness_result --> CANCELLED: Mandate revoked
    freshness_result --> CANCELLED: Budget exhausted
    freshness_result --> EXECUTING: Still valid
    
    EXECUTING --> OBSERVED: Outcome received
    
    state outcome_check <<choice>>
    OBSERVED --> outcome_check
    
    outcome_check --> RECOVERED: Payment success
    outcome_check --> RE_EVALUATE: Action failed
    outcome_check --> STOPPED: Budget exhausted
    
    RE_EVALUATE --> DIAGNOSED: Fresh state analysis
    
    RECOVERED --> [*]
    STOPPED --> [*]
    ESCALATED --> [*]
    CANCELLED --> [*]
    
    note right of RE_EVALUATE
        ONE next action selected
        from current state.
        Never a pre-planned sequence.
    end note
```

---

## Execution-Time Freshness Check

Critical for preventing stale actions:

```
10:00 → RecoverAI schedules retry for 10:10
10:05 → Customer pays manually via different channel
10:10 → Scheduler wakes up
10:10 → FRESHNESS CHECK
        ├── Case still open? → NO (already RECOVERED)
        └── Action: CANCELLED

Result: No duplicate charge, no customer confusion
```

### Freshness Check Components

| Check | Purpose |
|-------|---------|
| Case still open? | Prevent action on resolved case |
| Customer already recovered? | Prevent duplicate recovery attempts |
| Mandate still valid? | Subscription/recurring only |
| Retry/contact budget available? | Respect limits |
| Action still permitted by policy? | Policy may have changed |

---

## Complete Data Flow

```mermaid
sequenceDiagram
    participant E as External Event
    participant G as FastAPI Gateway
    participant R as Redis Queue
    participant W as Worker
    participant O as Orchestrator
    participant P as Policy Gate
    participant M as MCP Boundary
    participant Z as Razorpay API
    participant L as Langfuse
    participant D as PostgreSQL

    E->>G: Webhook / Synthetic Event
    G->>G: HMAC Verify
    G->>D: Store webhook_events (idempotent)
    G->>R: Push to recovery-events
    G-->>E: 200 OK
    
    W->>R: BLPOP recovery-events
    W->>D: Create/update recovery_case
    W->>O: run_orchestrator_for_case()
    
    rect rgb(240, 248, 255)
        Note over O: RecoverAI Engine
        O->>O: DIAGNOSE - Classify failure
        O->>O: SCORE - P0, Pa, Uplift, Net Value
        O->>P: POLICY - Filter by rules
        P-->>O: permitted_actions[]
        O->>O: LLM DECIDE - Groq analysis
        O->>O: VALIDATE - Hard checks
    end
    
    O->>D: Persist decision + reasoning
    O->>L: trace_orchestrator_run()
    
    alt Immediate Execution
        O->>M: execute_action(case_id, action)
        rect rgb(255, 248, 220)
            Note over M: MCP Boundary
            M->>M: FRESHNESS CHECK
            M->>M: CAPABILITY CHECK
            M->>M: IDEMPOTENCY CHECK
        end
        M->>Z: create_payment_link()
        Z-->>M: payment_link_id
        M->>D: Update attempt status
    else Scheduled Execution
        O->>D: INSERT scheduled_action
        Note over D: Timer fires at scheduled_for
        D->>W: Poll due actions
        W->>M: execute_action()
        rect rgb(255, 248, 220)
            Note over M: FRESHNESS CHECK
            alt Case already recovered
                M-->>W: CANCELLED
            else Still valid
                M->>Z: Execute action
            end
        end
    end
    
    Z->>G: payment_link.paid / payment.captured
    G->>W: Process outcome event
    
    alt Recovery Success
        W->>D: Case → RECOVERED
    else Recovery Failed
        W->>D: Case → RE_EVALUATE
        W->>R: Push to re-evaluate queue
        R->>W: Process re-evaluation
        W->>O: run_orchestrator_for_case()
        Note over O: Select ONE next action<br/>from fresh state
    end
```

---

## Technology Stack

| Layer | Technology | Purpose |
|-------|------------|---------|
| Gateway | FastAPI | Webhook receiver, API routes |
| Queue | Redis | Event bus, worker coordination |
| Persistence | PostgreSQL | Cases, attempts, scheduled actions |
| Orchestrator | LangGraph | Single-graph decision engine |
| LLM | Groq (Llama 3.1 8B) | Failure analysis + reasoning |
| Execution | MCP Server | Bounded tool capability |
| Payments | Razorpay Test Mode | Real payment operations |
| Observability | Langfuse | Trace dashboard |
| Frontend | React + Tailwind | Merchant dashboard |
