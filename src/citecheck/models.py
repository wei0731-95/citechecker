from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class CitationType(str, Enum):
    STATUTE = "STATUTE"
    JUDGMENT = "JUDGMENT"


class StatuteStatus(str, Enum):
    FOUND = "FOUND"
    LAW_NOT_FOUND = "LAW_NOT_FOUND"
    ARTICLE_NOT_FOUND = "ARTICLE_NOT_FOUND"
    ARTICLE_DELETED = "ARTICLE_DELETED"
    UNRESOLVED_REF = "UNRESOLVED_REF"
    LAW_NAME_UNCERTAIN = "LAW_NAME_UNCERTAIN"          # A1
    LAW_OUT_OF_SCOPE = "LAW_OUT_OF_SCOPE"              # A2
    LAW_REPEALED = "LAW_REPEALED"                      # A11
    PARAGRAPH_NOT_FOUND = "PARAGRAPH_NOT_FOUND"        # B1
    SUBPARAGRAPH_NOT_FOUND = "SUBPARAGRAPH_NOT_FOUND"  # B1


class JudgmentStatus(str, Enum):
    FOUND = "FOUND"
    NOT_FOUND_IN_SCOPE = "NOT_FOUND_IN_SCOPE"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    AMBIGUOUS = "AMBIGUOUS"
    DATA_NOT_COVERED = "DATA_NOT_COVERED"
    NOT_FOUND_CIVIL_ONLY = "NOT_FOUND_CIVIL_ONLY"      # A4


class StatuteL2Label(str, Enum):
    MATCHES = "MATCHES"
    MISMATCH = "MISMATCH"
    NOT_STATED = "NOT_STATED"
    UNCERTAIN = "UNCERTAIN"
    NOT_RUN = "NOT_RUN"


class StatuteRelation(str, Enum):  # the LLM may only answer one of these three
    CONSISTENT = "CONSISTENT"
    INCONSISTENT = "INCONSISTENT"
    NO_DESCRIPTION = "NO_DESCRIPTION"


class SegmentRole(str, Enum):
    MAIN_TEXT = "MAIN_TEXT"
    PARTY_CLAIM = "PARTY_CLAIM"
    LOWER_COURT = "LOWER_COURT"
    SUPREME_COURT = "SUPREME_COURT"
    UNKNOWN = "UNKNOWN"


class Relation(str, Enum):
    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    UNRELATED = "UNRELATED"


class L2Label(str, Enum):
    COURT_HOLDING = "COURT_HOLDING"
    PARTY_ARGUMENT = "PARTY_ARGUMENT"
    LOWER_COURT_VIEW = "LOWER_COURT_VIEW"
    CONTRADICTED = "CONTRADICTED"
    NOT_IN_JUDGMENT = "NOT_IN_JUDGMENT"
    UNCERTAIN = "UNCERTAIN"
    NOT_RUN = "NOT_RUN"


class Span(BaseModel):
    start: int  # offset in the NORMALIZED brief text, inclusive
    end: int    # exclusive


class SimilarJudgment(BaseModel):  # Phase G
    jid: str
    display: str
    passage: str
    confidence: float
    pdf_url: str | None = None


class OtherAuthority(BaseModel):  # A12
    raw: str
    span: Span
    original_span: Span | None = None
    kind: Literal["釋字", "憲判字", "決議", "座談會"]


class UnrecognizedSentence(BaseModel):  # A13
    text: str
    span: Span
    original_span: Span | None = None


class RunStats(BaseModel):  # F3
    api_calls: int = 0
    cache_hits: int = 0
    prep_new: int = 0
    skipped_by_cap: int = 0
    seconds: float = 0.0


class StatuteCitation(BaseModel):
    type: Literal["STATUTE"] = "STATUTE"
    raw: str
    span: Span
    law_name: str | None
    article: str | None
    paragraph: int | None = None
    subparagraph: int | None = None
    # Placeholder until verify.py runs (extract.py only sets UNRESOLVED_REF itself).
    status: StatuteStatus = StatuteStatus.LAW_NOT_FOUND
    article_text: str | None = None
    original_span: Span | None = None          # A8
    law_url: str | None = None                 # A9
    article_url: str | None = None             # A9
    not_in_force_note: bool = False            # A10
    structure_unchecked: bool = False          # B1: True = paragraph/subparagraph check not done
    claim_sentence: str | None = None          # B2
    l2_label: StatuteL2Label = StatuteL2Label.NOT_RUN
    l2_confidence: float | None = None


class L2Evidence(BaseModel):
    segment_index: int
    segment_role: SegmentRole
    passage: str  # at most 400 chars
    relation: Relation
    confidence: float


class JudgmentCitation(BaseModel):
    type: Literal["JUDGMENT"] = "JUDGMENT"
    raw: str
    span: Span
    court: str | None
    year: int | None
    case_word: str | None
    case_no: int | None
    doc_kind: str | None
    # Placeholder until verify.py runs (extract.py sets OUT_OF_SCOPE itself).
    status: JudgmentStatus = JudgmentStatus.NOT_FOUND_IN_SCOPE
    jid: str | None = None
    claim_sentence: str | None = None
    l2_label: L2Label = L2Label.NOT_RUN
    l2_evidence: L2Evidence | None = None
    original_span: Span | None = None                                  # A8
    domain_stated: Literal["民事", "刑事", "行政"] | None = None         # A4
    court_inferred: bool = False                                       # A6
    pdf_url: str | None = None                                         # A9
    main_text: str | None = None                                       # section 11
    procedural_dismissal: bool = False                                 # C3
    latest_year_note: bool = False                                     # A3
    similar: list[SimilarJudgment] = Field(default_factory=list)       # G


class DataCoverage(BaseModel):
    laws_update_date: str | None = None
    judgment_year_min: int | None = None
    judgment_year_max: int | None = None
    judgment_date_min: str | None = None
    judgment_date_max: str | None = None
    judgment_count: int = 0
    months_imported: list[str] = Field(default_factory=list)   # ["202305", "202602", ...]
    covered_years: list[int] = Field(default_factory=list)     # ROC years; see A3


class Report(BaseModel):
    input_file: str
    generated_at: str
    data_coverage: DataCoverage
    normalized_text: str = Field(description="Normalized brief text; all spans index into it")
    statutes: list[StatuteCitation]
    judgments: list[JudgmentCitation]
    warnings: list[str] = Field(default_factory=list)
    original_text: str = ""                                            # A8: text as pasted
    other_authorities: list[OtherAuthority] = Field(default_factory=list)
    unrecognized: list[UnrecognizedSentence] = Field(default_factory=list)
    llm_model: str | None = None
    versions: dict[str, int] = Field(default_factory=dict)
    stats: RunStats = Field(default_factory=RunStats)
