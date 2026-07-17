**BRIEFING_SacredConceptAtlas_2026-07-16.md**

---

**Project Name:** Sacred Concept Atlas

**Date:** 16 July 2026  
**Status:** Initial design phase (fun / personal knowledge project)  
**Primary Model Target:** Fable (in Cursor)

### 1. Project Vision

Build a structured, queryable **concept space** over the core religious texts of Judaism, Christianity, and Islam. The goal is to move beyond simple search and create a living map of **ideas, themes, and theological concepts** as they appear across these traditions.

Instead of treating the texts as flat documents, we will represent them as a graph where nodes carry **weighted conceptual signatures**. This allows us to ask questions like:
- Which concepts are shared across traditions vs. distinctive?
- How do certain ideas evolve or cluster?
- What is the conceptual "center of gravity" of a chapter or book?

The project is deliberately scoped to **just the texts + concept extraction + graph construction** for now. No UI, no agents running autonomously yet, and no prediction layer.

### 2. Core Data Model

#### 2.1 Text Ingestion Layer
- One table per major book (e.g., `genesis`, `quran_surah_1`, `gospel_of_john`, etc.).
- Each row represents a **section** (roughly chapter-length or thematically coherent unit — we decide granularity during ingestion).
- Columns should include at minimum:
  - `section_id`
  - `book`
  - `tradition` (Judaism / Christianity / Islam)
  - `section_number` or `reference`
  - `text` (full content of the section)
  - `metadata` (JSON)

#### 2.2 Retrieval Layer (Hybrid RAG)
- **BM25** for keyword / lexical matching.
- Local embedding model (e.g., `nomic-embed-text`, `mxbai-embed-large`, or `snowflake-arctic-embed`) for semantic search.
- Both indexes should be queryable together.

#### 2.3 Graph Layer — The Concept Space

**Nodes** = Sections (from the tables above).

Each node has:
- Standard metadata
- A set of **concepts** with **influence scores** that sum to **1.0**

Example:
```json
{
  "section_id": "genesis_1",
  "concepts": {
    "creation_order": 0.45,
    "divine_spoken_word": 0.30,
    "goodness_of_creation": 0.15,
    "separation_and_ordering": 0.10
  }
}
```

This weighting is important. It prevents repetitive or generic concepts from dominating just because they appear frequently.

**Edges** (initially light):
- Can be added later between sections that share high conceptual overlap.
- For now, focus on **node-level conceptual signatures**.

**Concept Hash / Registry**:
- A central store of all discovered concepts (starts empty).
- When processing a new section, the system checks whether the ideas in that section are already well-represented in the existing concept set.
- Agents (or a processing script) can **propose new concepts** only when confidence that the idea is novel exceeds a **rising threshold** (the more concepts already exist, the higher the bar to add a new one).

### 3. Critical Design Element: What Is a "Concept"?

This is the most important part to get right in the system prompt / Modelfile.

**Definition of a Concept (proposed):**
A *concept* is a **distinct, reusable theological or narrative idea** that can be meaningfully tracked across multiple sections and texts. It should be:
- Specific enough to be useful (not "God" or "good")
- Abstractable from the specific wording
- Theologically or narratively significant

**Good examples:**
- "Divine speech as creative act"
- "Covenant as conditional relationship"
- "Suffering as test of faith"
- "Prophetic warning and impending judgment"
- "Human dominion and stewardship"

**Bad examples (too vague or too specific):**
- "God" (too broad)
- "Adam and Eve eat the fruit" (too narrative-specific)

**Conceptual Similarity Rule:**
Two concepts should be considered the **same** if they express the same underlying idea, even if the language or emphasis differs slightly between traditions. The goal is to build a **shared conceptual vocabulary** across the three religions where appropriate, while still allowing tradition-specific concepts.

This definition and similarity criteria **must** be clearly and rigorously defined in the Modelfile / system prompt that processes the texts.

### 4. Processing Flow (High Level)

1. Ingest texts into structured tables + sections.
2. For each section:
   - Retrieve relevant existing concepts via hybrid search.
   - Ask the model to analyze the section and propose a weighted set of concepts (summing to 1.0).
   - For each proposed concept, decide:
     - Is it close enough to an existing concept? (Merge / adjust weights)
     - Is it genuinely new? Check against confidence threshold.
3. Update the node with its final concept distribution.
4. (Later) Update the global concept registry.

### 5. Key Technical & Prompt Engineering Challenges

- **Concept definition & granularity** — This needs to be extremely well-specified.
- **Influence score assignment** — The model must learn to distribute weight meaningfully rather than defaulting to uniform or overly broad concepts.
- **Rising confidence threshold** — As the concept registry grows, it should become harder to add new concepts. This prevents concept bloat.
- **Cross-tradition conceptual alignment** — The model needs guidance on when to treat similar ideas as the same concept vs. distinct.
- **Avoiding hallucinated or overly creative concepts** — Strong grounding in the actual text is required.

### 6. Scope (Strict for Now)

**In scope:**
- Text ingestion and structuring
- Hybrid RAG setup
- Concept extraction and weighting per section
- Building the concept registry with novelty detection
- Basic graph of sections with conceptual signatures

**Out of scope (for this phase):**
- Full agentic system running autonomously
- User interface / visualization
- Prediction or generation tasks
- Adding secondary literature or commentaries
- Complex graph algorithms (keep it simple at first)

### 7. Questions for Fable

When you review this, please help me think through:

1. How should we precisely define "concept" and "conceptual similarity" in the system prompt so the model is consistent and not overly creative or reductive?
2. What would be a good starting granularity for sections (e.g., full chapters, or smaller thematic units)?
3. How should we handle the rising confidence threshold for adding new concepts in practice?
4. Any recommendations on embedding model choice for this domain (theological/abstract language)?
5. Would you structure the concept registry as a simple JSON/dict, or something more structured (e.g., with embeddings of the concept definitions themselves)?

---

This briefing is written to be clear and actionable for Fable (or any strong reasoning model) in Cursor. 

Would you like me to adjust the tone, add more technical detail, or revise any section before you send it?