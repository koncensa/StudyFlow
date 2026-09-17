# StudyFlow 🎓

StudyFlow is an AI-assisted learning platform designed to help students study more effectively from their own course materials.

Students can upload PDF documents, generate AI-powered summaries, ask questions about their materials, create quizzes, analyze their performance, plan their study schedule, and use a Pomodoro timer within a single platform.

## ✨ Features

- 📄 PDF-based AI Assistant
- 📝 AI-generated summaries
  - Quick Summary
  - Explain Mode
  - Exam Focus
- ❓ AI-generated quizzes
- 📊 Quiz results and performance analysis
- 🎯 Topic-based strengths and weaknesses
- 📅 Academic Study Planner
- ⏱️ Pomodoro Study Timer
- 👤 User authentication and profiles
- 🏆 Progress and achievement system

## 🧠 AI & RAG

StudyFlow uses a Retrieval-Augmented Generation (RAG) approach to generate responses based on uploaded course materials.

The system processes PDF documents, splits their content into chunks, generates embeddings, and retrieves relevant sections before generating responses.

This helps keep answers focused on the student's uploaded study material.

## 🛠️ Tech Stack

### Frontend
- Angular
- TypeScript
- Bootstrap
- Chart.js

### Backend
- FastAPI
- Python
- SQLAlchemy
- JWT Authentication

### Database
- MySQL

### AI / Machine Learning
- Ollama
- Llama 3.2
- Phi-3 Mini
- FAISS
- nomic-embed-text
- Sentence Transformers

## 🏗️ Project Structure

    StudyFlow/
    ├── backend/       # FastAPI backend and AI services
    ├── frontend/      # Angular web application
    ├── scripts/       # Utility scripts
    ├── .gitignore
    └── package.json

## 🎯 Purpose

StudyFlow was developed as a Computer Engineering graduation project.

The goal of the project is to combine AI-assisted document learning, assessment, performance analysis, and study planning in one application.

## 🔒 Privacy

The application is designed around document-based learning. AI responses are generated using the student's uploaded learning materials, with local LLM support through Ollama.

## 👩‍💻 Developer

Developed by **Konce Ünsaç**  
Computer Engineering Graduate
