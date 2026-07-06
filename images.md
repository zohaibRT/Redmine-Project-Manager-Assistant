flowchart LR
    User[User message] --> Model[LLM]
    Model -->|needs data/action| Tool[Tool call]
    Tool -->|result| Model
    Model -->|done| Answer[Final answer]
