# model: quiz | tr: quiz denemesi mysql tabloları — ana deneme, konu sonuçları, soru cevapları / en: quiz attempt mysql tables main attempt topic results question answers

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text

from app.database.connection import Base


# model: QuizAttempt | tr: tek quiz denemesi özeti (skor, süre, hangi pdf) / en: single quiz attempt summary score duration linked pdf
class QuizAttempt(Base):
    __tablename__ = "quizzes"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    # tr: documents.id — isteğe bağlı pdf bağlantısı (db sütunu: document_id) / en: optional documents.id link db column document_id
    document_db_id = Column(
        "document_id",
        Integer,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    total_questions = Column(Integer, nullable=False)
    total_correct = Column("correct_answers", Integer, nullable=False)
    total_wrong = Column("wrong_answers", Integer, nullable=False)
    score_percentage = Column("score", Float, nullable=False)
    # tr: boş cevap sayısı ve süre — init_db migration ile eklenmiş olabilir / en: unanswered count and duration optional via init_db migration
    unanswered_count = Column(Integer, nullable=True)
    duration_seconds = Column(Integer, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


# model: QuizAttemptTopicResult | tr: bir denemede konu bazlı doğru/yanlış ve bant / en: per-topic correct wrong and band for one attempt
class QuizAttemptTopicResult(Base):
    __tablename__ = "topic_results"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    quiz_attempt_id = Column(
        "quiz_id",
        Integer,
        ForeignKey("quizzes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    topic = Column(String(255), nullable=False, index=True)
    correct_count = Column("correct_answers", Integer, nullable=False)
    wrong_count = Column("wrong_answers", Integer, nullable=False)
    total_attempts = Column("total_questions", Integer, nullable=False)

    success_rate = Column(Float, nullable=False)
    # tr: very_weak | weak | developing | good | strong / en: topic band label
    status = Column(String(20), nullable=False)


# model: QuizAttemptQuestionResult | tr: tek sorunun cevabı ve geri bildirim json'u / en: single question answer row and feedback json
class QuizAttemptQuestionResult(Base):
    __tablename__ = "quiz_answers"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    quiz_attempt_id = Column(
        "quiz_id",
        Integer,
        ForeignKey("quizzes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    question_index = Column("question_no", Integer, nullable=False)
    topic = Column(String(255), nullable=False, index=True)
    question_text = Column(Text, nullable=True)
    is_correct = Column(Boolean, nullable=False)
    selected_answer = Column(Text, nullable=False)
    correct_answer = Column(Text, nullable=False)
    # tr: why_wrong, hint, error_type vb. json string / en: feedback fields as json string why_wrong hint error_type
    feedback_json = Column("feedback", Text, nullable=True)
