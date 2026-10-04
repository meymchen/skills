---
name: system-design
description: Design systems, services, and architectures. Trigger with "design a system for", "how should we architect", "system design for", "what's the right architecture for", or when the user needs help with API design, data modeling, or service boundaries.
disable-model-invocation: false
user-invocable: true
---

<!-- Adapted by Yuming Chen on 2026-10-04 for personal use and dual-host support. See ../../UPSTREAM.md. -->

# System Design

> Read [CONNECTORS.md](../../CONNECTORS.md) for host invocation, personal use, and optional tools.

Help design systems and evaluate architectural decisions.

## Framework

### 1. Requirements Gathering

- Functional requirements (what it does)
- Non-functional requirements (scale, latency, availability, cost)
- Constraints (maintainer capacity, timeline, existing tech stack)

### 2. High-Level Design

- Component diagram
- Data flow
- API contracts
- Storage choices

### 3. Deep Dive

- Data model design
- API endpoint design (REST, GraphQL, gRPC)
- Caching strategy
- Queue/event design
- Error handling and retry logic

### 4. Scale and Reliability

- Load estimation
- Horizontal vs. vertical scaling
- Failover and redundancy
- Monitoring and alerting

### 5. Trade-off Analysis

- Every decision has trade-offs. Make them explicit.
- Consider: complexity, cost, maintainer familiarity, time to market, maintainability

## Output

Produce clear, structured design documents with diagrams (ASCII or described), explicit assumptions, and trade-off analysis. Always identify what you'd revisit as the system grows.
