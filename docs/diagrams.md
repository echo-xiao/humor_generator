# Humor Generator - System Diagrams

## 1. Problem Statement

```mermaid
graph TD
    ROOT["Current LLM-Only Humor Generation"] --> P1 & P2 & P3

    subgraph P1["Low Quality Output"]
        P1A["Generic / repetitive jokes"] --> P1B["No structural humor theory"]
        P1B --> P1C["Temperature randomness != real humor"]
    end

    subgraph P2["No Humor Knowledge"]
        P2A["No humor-specific KG"] --> P2B["No conflict / irony edges"]
        P2B --> P2C["No sentiment or domain annotation"]
    end

    subgraph P3["Weak Evaluation"]
        P3A["LLM self-evaluates own jokes"] --> P3B["Single-dimension scoring"]
        P3B --> P3C["No empathy / novelty metrics"]
    end

    P1C --> RESULT["Result: Unfunny, shallow,<br/>indistinguishable from templates"]
    P2C --> RESULT
    P3C --> RESULT

    style ROOT fill:#f03e3e,color:#fff
    style RESULT fill:#868e96,color:#fff
    style P1A fill:#ff6b6b,color:#fff
    style P2A fill:#ff6b6b,color:#fff
    style P3A fill:#ff6b6b,color:#fff
```

## 2. Strategy Overview

```mermaid
graph TD
    TOPIC["Input Topic"] --> SLOT["Find Humor Slot<br/>(humor_weight ranked)"]

    SLOT --> KG & SEM & CIL & OTH

    subgraph KG["Knowledge Graph Driven"]
        KG1["kg_contrast<br/>conflict triples"]
        KG2["causal_chain<br/>multi-hop cause"]
        KG3["expectation_violation<br/>expect vs reality"]
        KG4["ironic_reversal<br/>say A mean non-A"]
        KG5["self_contradiction<br/>purpose vs result"]
        KG6["hyperbolic_deflation<br/>grand to mundane"]
        KG7["self_deprecation<br/>own the pain"]
        KG8["xiehouyu<br/>riddle punchline"]
    end

    subgraph SEM["Semantic Driven"]
        SEM1["sememe_conflict<br/>HowNet tension pairs"]
        SEM2["ambiguity<br/>multi-sense polysemy"]
    end

    subgraph CIL["Cross-Domain Driven"]
        CIL1["context_shift<br/>Cilin domain clash"]
        CIL2["false_analogy<br/>build then break"]
    end

    subgraph OTH["Other Driven"]
        OTH1["homophone<br/>sound-alike puns"]
        OTH2["name_analysis<br/>character decompose"]
        OTH3["concretize<br/>absurd precision"]
    end

    KG1 & KG2 & KG3 & KG4 & KG5 & KG6 & KG7 & KG8 --> POOL["Candidate Pool"]
    SEM1 & SEM2 --> POOL
    CIL1 & CIL2 --> POOL
    OTH1 & OTH2 & OTH3 --> POOL

    POOL --> RAG["rag_replace<br/>meme store fusion"]
    RAG --> CRITIC["Critic Scoring + Refinement"]
    CRITIC --> OUT["Best Joke"]

    style TOPIC fill:#228be6,color:#fff
    style KG1 fill:#4c6ef5,color:#fff
    style KG2 fill:#4c6ef5,color:#fff
    style KG3 fill:#4c6ef5,color:#fff
    style KG4 fill:#4c6ef5,color:#fff
    style KG5 fill:#4c6ef5,color:#fff
    style KG6 fill:#4c6ef5,color:#fff
    style KG7 fill:#4c6ef5,color:#fff
    style KG8 fill:#4c6ef5,color:#fff
    style SEM1 fill:#ae3ec9,color:#fff
    style SEM2 fill:#ae3ec9,color:#fff
    style CIL1 fill:#2b8a3e,color:#fff
    style CIL2 fill:#2b8a3e,color:#fff
    style OTH1 fill:#e67700,color:#fff
    style OTH2 fill:#e67700,color:#fff
    style OTH3 fill:#e67700,color:#fff
    style OUT fill:#f03e3e,color:#fff
```

## 3. System Architecture

```mermaid
graph LR
    USER["User /<br/>Scheduler /<br/>Trending"] --> JG["Orchestrator"]

    JG --> KNOW

    subgraph KNOW["Knowledge Layer"]
        KG["Knowledge Graph<br/>195k nodes / 314k edges"] --> HN["HowNet Sememes"]
        HN --> CL["Cilin Taxonomy"]
        CL --> RAG["RAG Memes 1975"]
        RAG --> EXP["Graph Expander"]
    end

    KNOW --> SLOT["Find Humor Slots<br/>ranked by<br/>humor_weight"]

    SLOT --> CTX["Build Context<br/>slots + triples<br/>+ memes"]

    CTX --> GEN

    subgraph GEN["Generation Layer"]
        direction TB
        M["15 Strategy Methods<br/>all data-driven"]
        M --> CAND["Candidate Pool<br/>15+ jokes"]
    end

    GEN --> EVAL

    subgraph EVAL["Evaluation Layer"]
        direction TB
        SC["5-Dim Scoring<br/>humor / relevance<br/>natural / empathy<br/>novelty"]
        SC --> REF["Adversarial Refinement<br/>critique -> rewrite<br/>-> rescore"]
    end

    EVAL --> OUT["Best Joke<br/>Output"]

    M -.-> API["Gemini 2.5 Pro"]
    SC -.-> API

    style USER fill:#228be6,color:#fff
    style SLOT fill:#f03e3e,color:#fff
    style M fill:#4c6ef5,color:#fff
    style SC fill:#ae3ec9,color:#fff
    style OUT fill:#2b8a3e,color:#fff
    style API fill:#fab005,color:#000
```

## 4. Data Flow

```mermaid
graph TD
    subgraph COLLECT["Collection Layer"]
        YT["YouTube Standup"] -->|subtitle / FunASR| TXT1["transcript .txt"]
        XHS["Xiaohongshu Video"] -->|Gemini transcribe| TXT2["transcript .txt"]
        IMG["Image Posts"] -->|OCR| TXT3["text .txt"]
        MEMES["Meme Libraries"] -->|extract| TXT4["meme .txt"]
    end

    subgraph EXTERNAL["External Data Import"]
        CN["ConceptNet 5.7"] -->|filter zh + t2s| CN_J["conceptnet_zh.jsonl<br/>272k triples"]
        XHY["Xiehouyu Dict"] --> XHY_J["xiehouyu.jsonl<br/>14k"]
        CY["Idiom Dict"] --> CY_J["chengyu.jsonl<br/>55k"]
        PY["jieba + pypinyin"] --> HP_J["homophone.jsonl<br/>14k"]
        DLUT["DLUT Sentiment"] --> SENT["sentiment.json<br/>27k words"]
        CIL["Cilin Package"] --> CIL_J["cilin.json<br/>77k words"]
    end

    TXT1 & TXT2 & TXT3 -->|upload| GCS["GCS raw_data/"]
    GCS -->|Gemini extract| TRIP["Humor Triples JSONL<br/>conflict / irony / cause"]
    TXT4 --> RAG_J["RAG-ready JSONL<br/>1975 memes"]

    TRIP --> MERGE["Merge All Triples<br/>358k total"]
    CN_J --> MERGE
    XHY_J --> MERGE
    CY_J --> MERGE
    HP_J --> MERGE

    MERGE --> BUILD["Build NetworkX Graph<br/>195k nodes / 314k edges"]

    SENT -->|annotate nodes| BUILD
    CIL_J -->|annotate nodes| BUILD

    BUILD --> HW["Compute humor_weight<br/>source priority + sentiment<br/>+ cross-domain + relation"]

    HW --> PKL["knowledge_graph.pkl<br/>saved local + GCS"]

    PKL --> RUNTIME["Runtime: Generate Jokes"]
    RAG_J --> RUNTIME

    style GCS fill:#228be6,color:#fff
    style TRIP fill:#4c6ef5,color:#fff
    style BUILD fill:#ae3ec9,color:#fff
    style PKL fill:#f03e3e,color:#fff
    style RUNTIME fill:#2b8a3e,color:#fff
```

## 5. Ideal Knowledge Graph

```mermaid
graph TD
    T1(("marriage<br/>src: humor +5"))
    T2(("work<br/>src: humor +5"))
    T3(("diet<br/>src: humor +5"))

    H1(("happiness<br/>sent +1.0"))
    H2(("financial<br/>pressure<br/>sent -0.8"))
    H3(("housework<br/>negotiation"))
    W1(("self-fulfill<br/>sent +1.0"))
    W2(("cervical<br/>disease<br/>sent -1.0"))
    W3(("time rental<br/>domain: Econ"))
    D1(("beauty"))
    D2(("revenge<br/>binge"))

    C1(("buy house"))
    C2(("earn money"))
    PH1(("jie-hun<br/>= cut soul"))
    XH1(("wedding dish"))
    XH2(("served cold"))

    %% Positive expectations
    T1 -->|"expects"| H1
    T2 -->|"purpose_is"| W1
    T3 -->|"purpose_is"| D1

    %% Negative reality
    T1 -->|"actually"| H2
    T2 -->|"causes"| W2
    T3 -->|"causes"| D2

    %% Conflict lines: positive <--> negative
    H1 <-->|"CONFLICT<br/>sentiment gap = 1.8"| H2
    W1 <-->|"CONFLICT<br/>sentiment gap = 2.0"| W2
    D1 <-->|"CONFLICT<br/>purpose vs result"| D2

    %% Other humor edges
    T1 -->|"essence_is"| H3
    T2 -->|"equals"| W3
    T1 -.-|"sounds_like"| PH1
    XH1 ==>|"xiehouyu"| XH2
    T1 -.-> XH1

    %% Common sense (low weight)
    T1 -.->|"CN +1"| C1
    T2 -.->|"CN +1"| C2

    style T1 fill:#f03e3e,color:#fff,stroke-width:4px
    style T2 fill:#f03e3e,color:#fff,stroke-width:4px
    style T3 fill:#f03e3e,color:#fff,stroke-width:4px
    style H1 fill:#51cf66,color:#fff
    style H2 fill:#ff6b6b,color:#fff
    style H3 fill:#ffa94d,color:#fff
    style W1 fill:#51cf66,color:#fff
    style W2 fill:#ff6b6b,color:#fff
    style W3 fill:#ffa94d,color:#fff
    style D1 fill:#51cf66,color:#fff
    style D2 fill:#ff6b6b,color:#fff
    style C1 fill:#ced4da,color:#333
    style C2 fill:#ced4da,color:#333
    style PH1 fill:#748ffc,color:#fff
    style XH1 fill:#f783ac,color:#fff
    style XH2 fill:#f783ac,color:#fff

    linkStyle 0 stroke:#51cf66,stroke-width:2px
    linkStyle 1 stroke:#51cf66,stroke-width:2px
    linkStyle 2 stroke:#51cf66,stroke-width:2px
    linkStyle 3 stroke:#ff6b6b,stroke-width:2px
    linkStyle 4 stroke:#ff6b6b,stroke-width:2px
    linkStyle 5 stroke:#ff6b6b,stroke-width:2px
    linkStyle 6 stroke:#f03e3e,stroke-width:4px,stroke-dasharray:8
    linkStyle 7 stroke:#f03e3e,stroke-width:4px,stroke-dasharray:8
    linkStyle 8 stroke:#f03e3e,stroke-width:4px,stroke-dasharray:8
    linkStyle 9 stroke:#ffa94d,stroke-width:2px
    linkStyle 10 stroke:#ffa94d,stroke-width:2px
    linkStyle 11 stroke:#748ffc,stroke-width:2px,stroke-dasharray:5
    linkStyle 12 stroke:#f783ac,stroke-width:3px
    linkStyle 13 stroke:#ced4da,stroke-width:1px,stroke-dasharray:5
    linkStyle 14 stroke:#ced4da,stroke-width:1px,stroke-dasharray:5
    linkStyle 15 stroke:#ced4da,stroke-width:1px,stroke-dasharray:5
```

## 6. Evaluation Framework

```mermaid
graph TD
    CAND["Candidate Jokes<br/>(15+ from all strategies)"] --> SCORE

    subgraph SCORE["Multi-Dimensional Scoring (each 1-5)"]
        D1["Humor<br/>surprise / incongruity /<br/>absurdity"]
        D2["Relevance<br/>on-topic / slot alignment"]
        D3["Naturalness<br/>colloquial / standup tone"]
        D4["Empathy (highest weight)<br/>pain_frequency weighted /<br/>negative nodes prioritized"]
        D5["Novelty (lowest weight)<br/>cosine distance from<br/>existing joke corpus"]
    end

    SCORE --> RANK["Rank by Weighted Total<br/>empathy > humor > natural<br/>> relevance > novelty"]

    RANK --> TOP["Top-K Candidates"]

    TOP --> REFINE

    subgraph REFINE["Adversarial Refinement"]
        R1["Critic generates<br/>actionable + specific feedback"]
        R1 --> R2["Generator rewrites<br/>based on critique"]
        R2 --> R3["Re-score new version"]
        R3 -->|"improved"| R4["Accept new version"]
        R3 -->|"not improved"| R5["Keep original"]
    end

    REFINE --> FINAL["Final Output<br/>with score breakdown"]

    subgraph SIGNALS["Underlying Signals"]
        S1["humor_weight<br/>relation + source + sentiment<br/>+ cross-domain + degree"]
        S2["Empathy Score<br/>avg pain_frequency<br/>of involved nodes"]
        S3["Simplicity<br/>word count / sentence count"]
        S4["Benchmark Target<br/>CHumor 2.0: human 78%<br/>vs best LLM 60%"]
    end

    SIGNALS -.-> SCORE

    style CAND fill:#228be6,color:#fff
    style D4 fill:#f03e3e,color:#fff
    style RANK fill:#ae3ec9,color:#fff
    style R4 fill:#2b8a3e,color:#fff
    style R5 fill:#868e96,color:#fff
    style FINAL fill:#2b8a3e,color:#fff
```

## 7. Final Output Vision

```mermaid
graph TD
    INPUT["Topic: work<br/>Source: trending / user / scheduled"] --> SLOT["Humor Slot: cervical spondylosis<br/>humor_weight = 12.0<br/>source = humor corpus"]

    SLOT --> GEN["15 Strategies Generate in Parallel"]

    GEN --> J1["[kg_contrast]<br/>Work is trading your spine<br/>for a mortgage"]
    GEN --> J2["[expectation]<br/>Thought work meant self-fulfillment,<br/>turns out it meant food delivery freedom"]
    GEN --> J3["[self_contradiction]<br/>Work is for a better life,<br/>but after work there is no life"]
    GEN --> J4["[hyperbolic_deflation]<br/>After 10 years of hustle, finally<br/>achieved on-time food delivery"]
    GEN --> J5["[homophone]<br/>shangban sounds like shangban<br/>(injury shift)"]
    GEN --> J6["... 10 more strategies"]

    J1 & J2 & J3 & J4 & J5 & J6 --> CRITIC["Critic Scoring<br/>humor=5 relevance=4 natural=5<br/>empathy=5 novelty=4"]

    CRITIC -->|"best: 14/15"| REFINE["Refine Round 1"]

    REFINE --> FINAL["Work is for a better life,<br/>but after you start working,<br/>what life means doesn't matter --<br/>what matters is staying alive to go to work."]

    FINAL -.-> F1["Auto-post Xiaohongshu"]
    FINAL -.-> F2["Match images from<br/>Google Drive"]
    FINAL -.-> F3["Tune sharpness by<br/>real-time social sentiment"]

    style INPUT fill:#228be6,color:#fff
    style SLOT fill:#f03e3e,color:#fff
    style GEN fill:#4c6ef5,color:#fff
    style CRITIC fill:#ae3ec9,color:#fff
    style FINAL fill:#2b8a3e,color:#fff,stroke-width:3px
    style F1 fill:#868e96,color:#fff
    style F2 fill:#868e96,color:#fff
    style F3 fill:#868e96,color:#fff
```
