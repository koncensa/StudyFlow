CREATE DATABASE studyflow;
USE studyflow;

-- USERS
CREATE TABLE users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    full_name VARCHAR(120),
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- USER PROFILE
CREATE TABLE profiles (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL UNIQUE,
    xp INT NOT NULL DEFAULT 0,
    language_code VARCHAR(10) NOT NULL DEFAULT 'en',
    bio TEXT NULL,
    profile_photo_path VARCHAR(512) NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- DOCUMENTS (PDF)
CREATE TABLE documents (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    title VARCHAR(255),
    session_key VARCHAR(128) NOT NULL UNIQUE,
    study_text LONGTEXT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- CHAT MESSAGES (per user + document; never shared across accounts)
CREATE TABLE messages (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    document_id INT NOT NULL,
    sender VARCHAR(20) NOT NULL,
    mode VARCHAR(20) NULL,
    content TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE,
    INDEX idx_messages_user_id (user_id),
    INDEX idx_messages_user_document (user_id, document_id),
    INDEX idx_messages_created_at (created_at)
);

-- QUIZ ATTEMPTS
CREATE TABLE quizzes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    document_id INT,
    total_questions INT NOT NULL,
    correct_answers INT NOT NULL DEFAULT 0,
    wrong_answers INT NOT NULL DEFAULT 0,
    unanswered_count INT NOT NULL DEFAULT 0,
    duration_seconds INT NULL,
    score FLOAT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
);

-- QUIZ ANSWERS
CREATE TABLE quiz_answers (
    id INT AUTO_INCREMENT PRIMARY KEY,
    quiz_id INT NOT NULL,
    question_no INT NOT NULL,
    topic VARCHAR(255) NOT NULL,
    question_text TEXT,
    selected_answer TEXT NOT NULL,
    correct_answer TEXT NOT NULL,
    is_correct TINYINT(1) NOT NULL,
    feedback TEXT,
    FOREIGN KEY (quiz_id) REFERENCES quizzes(id) ON DELETE CASCADE
);

-- TOPIC RESULTS
CREATE TABLE topic_results (
    id INT AUTO_INCREMENT PRIMARY KEY,
    quiz_id INT NOT NULL,
    topic VARCHAR(255) NOT NULL,
    total_questions INT NOT NULL,
    correct_answers INT NOT NULL,
    wrong_answers INT NOT NULL,
    success_rate FLOAT NOT NULL,
    status VARCHAR(20) NOT NULL,
    FOREIGN KEY (quiz_id) REFERENCES quizzes(id) ON DELETE CASCADE
);

-- DOCUMENT SUMMARY HISTORY (per user + document; never shared across accounts)
CREATE TABLE document_summary_history (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    document_id INT NOT NULL,
    mode VARCHAR(24) NOT NULL,
    level VARCHAR(24) NOT NULL,
    output_locale VARCHAR(10) NOT NULL DEFAULT 'en',
    content TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE,
    INDEX idx_dsh_user_id (user_id),
    INDEX idx_dsh_user_document (user_id, document_id),
    INDEX idx_dsh_mode (mode),
    INDEX idx_dsh_level (level),
    INDEX idx_dsh_created_at (created_at)
);

-- RECOMMENDATIONS
CREATE TABLE recommendations (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    document_id INT NOT NULL,
    topic VARCHAR(255) NOT NULL,
    type VARCHAR(50) NOT NULL,
    content TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
);

-- POMODORO
CREATE TABLE pomodoro_logs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    topic VARCHAR(255),
    start_time DATETIME NOT NULL,
    end_time DATETIME,
    duration_seconds INT,
    status VARCHAR(20) NOT NULL DEFAULT 'running',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- ACADEMIC STUDY PLAN
CREATE TABLE academic_study_plans (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    course_name VARCHAR(255) NOT NULL,
    goal_text TEXT NOT NULL,
    deadline_date DATE NOT NULL,
    daily_hours DOUBLE NOT NULL,
    study_days_json VARCHAR(256) NOT NULL,
    weak_topics_json TEXT NOT NULL,
    confident_topics_json TEXT NOT NULL,
    topic_outline_json TEXT NOT NULL,
    tasks_json LONGTEXT NOT NULL,
    is_finished TINYINT(1) NOT NULL DEFAULT 0,
    finished_at DATETIME NULL,
    completed_count INT NOT NULL DEFAULT 0,
    missed_count INT NOT NULL DEFAULT 0,
    total_task_count INT NOT NULL DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_academic_plans_user (user_id),
    INDEX idx_academic_plans_user_updated (user_id, updated_at)
);

-- BADGES
CREATE TABLE badges (
    id INT AUTO_INCREMENT PRIMARY KEY,
    code VARCHAR(50) NOT NULL,
    title VARCHAR(100) NOT NULL,
    description TEXT NOT NULL
);

-- USER BADGES
CREATE TABLE user_badges (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    badge_id INT NOT NULL,
    earned_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (badge_id) REFERENCES badges(id) ON DELETE CASCADE
);

-- SAMPLE DATA
INSERT INTO badges (code, title, description) VALUES
('first_pdf', 'First Upload', 'Uploaded your first PDF'),
('first_quiz', 'First Quiz', 'Completed your first quiz'),
('focus', 'First Focus', 'Finished your first study session'),
('quiz_rookie', 'Quiz Rookie', 'Completed 5 quizzes'),
('quiz_marathon', 'Quiz Marathon', 'Completed 20 quizzes'),
('deep_focus', 'Deep Focus', 'Completed 5 Pomodoro sessions'),
('focus_legend', 'Focus Legend', 'Completed 25 Pomodoro sessions'),
('sharp_mind', 'Sharp Mind', 'Scored 90% or above in a quiz'),
('comeback', 'Comeback', 'Improved your score'),
('weak_topic_crusher', 'Weak Topic Crusher', 'Improved a weak topic'),
('consistent_learner', 'Consistent Learner', 'Studied consistently'),
('smart_improver', 'Smart Improver', 'Followed advice and improved'),
('planner', 'Planner', 'Created a study plan');