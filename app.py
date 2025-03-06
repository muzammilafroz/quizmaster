import os
import logging
import pandas as pd
from datetime import datetime
from werkzeug.utils import secure_filename
from flask import Flask, render_template, redirect, url_for, flash, request, send_from_directory, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy.orm import DeclarativeBase

# Configure logging
logging.basicConfig(level=logging.DEBUG)

# Initialize Flask app
class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret-key")

# Database configuration
database_url = os.environ.get("DATABASE_URL", "sqlite:///quizmaster.db")
app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Set engine options based on database type
if database_url.startswith('postgresql'):
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_pre_ping": True,  # Test connections before using them
        "pool_recycle": 300,    # Recycle connections after 5 minutes
        "max_overflow": 15,     # Allow 15 connections beyond pool_size
    }

# File upload configuration
UPLOAD_FOLDER = 'static/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'csv', 'xlsx'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Ensure upload directory exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Initialize extensions
db.init_app(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'user_login'

# Import models and forms
from models import User, Admin, Subject, Chapter, Quiz, Question, Score
from forms import (LoginForm, RegisterForm, SubjectForm, ChapterForm, QuizForm, 
                  QuestionForm, QuestionImportForm, UserProfileForm)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def handle_file_upload(file, folder=''):
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        path = os.path.join(app.config['UPLOAD_FOLDER'], folder, filename)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        file.save(path)
        return os.path.join(folder, filename) if folder else filename
    return None

@app.route('/admin/quizzes/<int:quiz_id>/questions', methods=['GET', 'POST'])
@login_required
def manage_questions(quiz_id):
    if not isinstance(current_user, Admin):
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('user_dashboard'))

    quiz = Quiz.query.get_or_404(quiz_id)
    questions = Question.query.filter_by(quiz_id=quiz_id).all()

    question_form = QuestionForm()
    question_form.quiz_id.choices = [(quiz_id, quiz.chapter.name)]  # Set choices for the quiz_id field
    question_form.quiz_id.data = quiz_id

    import_form = QuestionImportForm()
    import_form.quiz_id.choices = [(quiz_id, quiz.chapter.name)]  # Set choices for the quiz_id field
    import_form.quiz_id.data = quiz_id

    if request.method == 'POST':
        form_type = request.form.get('form_type')
        logging.debug(f"Form type: {form_type}")
        logging.debug(f"Form data: {request.form}")
        logging.debug(f"Files: {request.files}")

        if form_type == 'question':
            if question_form.validate_on_submit():
                try:
                    question_image_path = None
                    if question_form.question_image.data:
                        question_image_path = handle_file_upload(
                            question_form.question_image.data, 
                            folder=f'questions/quiz_{quiz_id}'
                        )

                    question = Question(
                        quiz_id=quiz_id,
                        question_statement=question_form.question_statement.data,
                        question_image=question_image_path,
                        option_1=question_form.option_1.data,
                        option_2=question_form.option_2.data,
                        option_3=question_form.option_3.data,
                        option_4=question_form.option_4.data,
                        correct_option=int(question_form.correct_option.data)
                    )
                    db.session.add(question)
                    db.session.commit()
                    flash('Question added successfully!', 'success')
                except Exception as e:
                    db.session.rollback()
                    flash(f'Error adding question: {str(e)}', 'danger')
                    logging.error(f"Error adding question: {str(e)}")
            else:
                for field, errors in question_form.errors.items():
                    for error in errors:
                        flash(f'{field}: {error}', 'danger')
                        logging.error(f"Form validation error - {field}: {error}")

        elif form_type == 'import':
            if import_form.validate_on_submit():
                file_path = handle_file_upload(import_form.file.data)
                if file_path:
                    try:
                        if file_path.endswith('.csv'):
                            df = pd.read_csv(os.path.join(app.config['UPLOAD_FOLDER'], file_path), dtype=str)
                        else:  # Excel file
                            df = pd.read_excel(os.path.join(app.config['UPLOAD_FOLDER'], file_path), dtype=str)

                        required_columns = ['question_statement', 'option_1', 'option_2', 'option_3', 'option_4', 'correct_option']
                        if not all(col in df.columns for col in required_columns):
                            missing_cols = [col for col in required_columns if col not in df.columns]
                            raise ValueError(f"Missing required columns: {', '.join(missing_cols)}")

                        for index, row in df.iterrows():
                            try:
                                logging.info(f"Processing row {index + 1}: {row}")

                                # Ensure all required fields are present and not empty
                                for field in required_columns:
                                    if pd.isna(row[field]) or str(row[field]).strip() == '':
                                        raise ValueError(f"Field '{field}' is required but empty")

                                # Convert correct_option to integer after validation
                                try:
                                    correct_option = int(float(row['correct_option']))
                                    if not 1 <= correct_option <= 4:
                                        raise ValueError(f"Correct option must be between 1 and 4, got {correct_option}")
                                except (ValueError, TypeError) as e:
                                    db.session.rollback()
                                    raise ValueError(f"Invalid correct_option value in row {index + 1}: {str(e)}")

                                # Create and add question with proper type handling
                                question = Question(
                                    quiz_id=quiz_id,
                                    question_statement=str(row['question_statement']).strip(),
                                    question_image=None if pd.isna(row.get('image_url')) else str(row.get('image_url')),
                                    option_1=str(row['option_1']).strip(),
                                    option_2=str(row['option_2']).strip(),
                                    option_3=str(row['option_3']).strip(),
                                    option_4=str(row['option_4']).strip(),
                                    correct_option=correct_option
                                )
                                db.session.add(question)
                                logging.info(f"Successfully added question from row {index + 1}")

                            except Exception as row_error:
                                db.session.rollback()
                                logging.error(f"Error processing row {index + 1}: {row}")
                                logging.error(f"Error details: {str(row_error)}")
                                raise ValueError(f"Error in row {index + 1}: {str(row_error)}")

                        db.session.commit()
                        flash(f'Successfully imported {len(df)} questions!', 'success')
                        logging.info(f"Successfully imported {len(df)} questions")

                    except Exception as e:
                        db.session.rollback()
                        flash(f'Error importing questions: {str(e)}', 'danger')
                        logging.error(f"Error importing questions: {str(e)}")
                    finally:
                        if file_path and os.path.exists(os.path.join(app.config['UPLOAD_FOLDER'], file_path)):
                            os.remove(os.path.join(app.config['UPLOAD_FOLDER'], file_path))

            else:
                for field, errors in import_form.errors.items():
                    for error in errors:
                        flash(f'{field}: {error}', 'danger')
                        logging.error(f"Import form validation error - {field}: {error}")

        return redirect(url_for('manage_questions', quiz_id=quiz_id))

    return render_template('admin/questions.html', 
                         quiz=quiz, 
                         questions=questions,
                         question_form=question_form,
                         import_form=import_form)

# Add route for serving uploaded files
@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@login_manager.user_loader
def load_user(user_id):
    try:
        # First try to load as admin
        user = Admin.query.get(int(user_id))
        if user:
            return user
        # If not admin, try as regular user
        return User.query.get(int(user_id))
    except:
        return None

# Authentication routes
@app.route('/')
def index():
    if current_user.is_authenticated:
        # Always redirect to user dashboard regardless of who is logged in
        # Admin must explicitly go to /admin
        return redirect(url_for('user_dashboard'))
    return redirect(url_for('user_login'))

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    # If already logged in as admin, redirect to admin dashboard
    if current_user.is_authenticated and isinstance(current_user, Admin):
        return redirect(url_for('admin_dashboard'))
    
    # For user, we'll show the admin login page instead of redirecting
    form = LoginForm()
    if form.validate_on_submit():
        if form.email.data != 'admin':
            flash('Invalid admin username', 'danger')
            return redirect(url_for('admin_login'))

        admin = Admin.query.filter_by(username='admin').first()
        if admin and check_password_hash(admin.password, form.password.data):
            # Login as admin without logging out the current user
            login_user(admin)
            return redirect(url_for('admin_dashboard'))
        flash('Invalid admin credentials', 'danger')
    return render_template('auth/admin_login.html', form=form)

@app.route('/login', methods=['GET', 'POST'])
def user_login():
    # If already logged in as user, redirect to user dashboard
    if current_user.is_authenticated and isinstance(current_user, User):
        return redirect(url_for('user_dashboard'))
    # If already logged in as admin, show user login page
    
    form = LoginForm()
    if form.validate_on_submit():
        try:
            user = User.query.filter_by(email=form.email.data).first()
            if user and check_password_hash(user.password, form.password.data):
                # Login as user without logging out the current admin
                login_user(user)
                return redirect(url_for('user_dashboard'))
            flash('Invalid email or password', 'danger')
        except Exception as e:
            logging.error(f"Database error during login: {str(e)}")
            flash('Login failed due to a server error. Please try again later.', 'danger')
            db.session.rollback()  # Roll back any failed transaction
    return render_template('auth/login.html', form=form)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('user_dashboard'))

    form = RegisterForm()
    if form.validate_on_submit():
        if User.query.filter_by(email=form.email.data).first():
            flash('Email already registered', 'danger')
            return redirect(url_for('register'))

        user = User(
            email=form.email.data,
            password=generate_password_hash(form.password.data),
            full_name=form.full_name.data,
            qualification=form.qualification.data,
            dob=form.dob.data
        )
        db.session.add(user)
        db.session.commit()
        flash('Registration successful!', 'success')
        return redirect(url_for('user_login'))
    return render_template('auth/register.html', form=form)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('user_login'))

# Admin routes
@app.route('/admin')
def admin_dashboard():
    # If logged in as admin, show dashboard
    if current_user.is_authenticated and isinstance(current_user, Admin):
        subjects = Subject.query.all()
        users = User.query.all()
        total_quizzes = Quiz.query.count()
        return render_template('admin/dashboard.html', subjects=subjects, users=users, total_quizzes=total_quizzes)
    # If not logged in or logged in as user, redirect to admin login
    return redirect(url_for('admin_login'))

@app.route('/admin/subjects', methods=['GET', 'POST'])
@login_required
def manage_subjects():
    if not isinstance(current_user, Admin):
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('user_dashboard'))

    form = SubjectForm()
    if form.validate_on_submit():
        subject = Subject(name=form.name.data, description=form.description.data)
        db.session.add(subject)
        db.session.commit()
        flash('Subject added successfully!', 'success')
        return redirect(url_for('manage_subjects'))

    subjects = Subject.query.all()
    return render_template('admin/subjects.html', form=form, subjects=subjects)

@app.route('/admin/chapters', methods=['GET', 'POST'])
@login_required
def manage_chapters():
    if not isinstance(current_user, Admin):
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('user_dashboard'))

    form = ChapterForm()
    # Populate subject choices
    form.subject_id.choices = [(s.id, s.name) for s in Subject.query.all()]

    if form.validate_on_submit():
        chapter = Chapter(
            subject_id=form.subject_id.data,
            name=form.name.data,
            description=form.description.data
        )
        db.session.add(chapter)
        db.session.commit()
        flash('Chapter added successfully!', 'success')
        return redirect(url_for('manage_chapters'))

    chapters = Chapter.query.all()
    return render_template('admin/chapters.html', form=form, chapters=chapters)

@app.route('/admin/quizzes', methods=['GET', 'POST'])
@login_required
def manage_quizzes():
    if not isinstance(current_user, Admin):
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('user_dashboard'))

    form = QuizForm()
    # Populate chapter choices
    form.chapter_id.choices = [(c.id, f"{c.subject.name} - {c.name}") for c in Chapter.query.all()]

    if form.validate_on_submit():
        quiz = Quiz(
            chapter_id=form.chapter_id.data,
            date_of_quiz=form.date_of_quiz.data,
            time_duration=form.time_duration.data,
            remarks=form.remarks.data
        )
        db.session.add(quiz)
        db.session.commit()
        flash('Quiz added successfully!', 'success')
        return redirect(url_for('manage_quizzes'))

    quizzes = Quiz.query.all()
    return render_template('admin/quizzes.html', form=form, quizzes=quizzes)

@app.route('/admin/users')
@login_required
def manage_users():
    if not isinstance(current_user, Admin):
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('user_dashboard'))

    users = User.query.all()
    return render_template('admin/users.html', users=users)


# User routes
@app.route('/dashboard')
@login_required
def user_dashboard():
    if isinstance(current_user, Admin):
        return redirect(url_for('admin_dashboard'))
    
    # Get all subjects with chapters and quizzes
    subjects = Subject.query.all()
    
    # Get recent scores for the user
    recent_scores = Score.query.filter_by(user_id=current_user.id).order_by(Score.time_stamp_of_attempt.desc()).limit(5).all()
    
    return render_template('user/dashboard.html', subjects=subjects, recent_scores=recent_scores)

@app.route('/quiz/<int:quiz_id>')
@login_required
def take_quiz(quiz_id):
    if isinstance(current_user, Admin):
        return redirect(url_for('admin_dashboard'))
        
    # Check if user has already taken this quiz
    existing_score = Score.query.filter_by(user_id=current_user.id, quiz_id=quiz_id).first()
    if existing_score:
        flash('You have already taken this quiz. You can review your results.', 'info')
        return redirect(url_for('view_score', score_id=existing_score.id))
        
    quiz = Quiz.query.get_or_404(quiz_id)
    questions = Question.query.filter_by(quiz_id=quiz_id).all()
    
    if not questions:
        flash('This quiz has no questions yet.', 'warning')
        return redirect(url_for('user_dashboard'))
        
    return render_template('user/quiz.html', quiz=quiz, questions=questions)

@app.route('/quiz/<int:quiz_id>/submit', methods=['POST'])
@login_required
def submit_quiz(quiz_id):
    if isinstance(current_user, Admin):
        return redirect(url_for('admin_dashboard'))
        
    quiz = Quiz.query.get_or_404(quiz_id)
    questions = Question.query.filter_by(quiz_id=quiz_id).all()
    
    correct_answers = 0
    wrong_answers = 0
    not_attempted = 0
    
    # Store user answers for later review
    user_answers = {}
    
    for question in questions:
        answer_key = f'answer_{question.id}'
        if answer_key in request.form:
            user_answer = int(request.form[answer_key])
            user_answers[question.id] = user_answer
            
            if user_answer == question.correct_option:
                correct_answers += 1
            else:
                wrong_answers += 1
        else:
            not_attempted += 1
    
    # Calculate score percentage
    total_score = 0
    if len(questions) > 0:
        total_score = round((correct_answers / len(questions)) * 100)
    
    # Calculate accuracy
    attempted = correct_answers + wrong_answers
    accuracy = 0
    if attempted > 0:
        accuracy = round((correct_answers / attempted) * 100)
    
    # Save score to database
    score = Score(
        quiz_id=quiz_id,
        user_id=current_user.id,
        total_scored=total_score
    )
    db.session.add(score)
    db.session.commit()
    
    # Get progress data for chart
    recent_scores = Score.query.filter_by(user_id=current_user.id).order_by(Score.time_stamp_of_attempt.desc()).limit(5).all()
    recent_scores.reverse()  # Show oldest to newest for progression
    
    progress_labels = [s.time_stamp_of_attempt.strftime('%d/%m/%Y') for s in recent_scores]
    progress_data = [s.total_scored for s in recent_scores]
    
    # Store user's answers in session for review
    session[f'quiz_{quiz_id}_answers'] = user_answers
    
    return render_template('user/results.html', 
                          quiz=quiz,
                          score=score,
                          correct_answers=correct_answers,
                          wrong_answers=wrong_answers,
                          not_attempted=not_attempted,
                          total_questions=len(questions),
                          accuracy=accuracy,
                          progress_labels=progress_labels,
                          progress_data=progress_data)

@app.route('/score/<int:score_id>')
@login_required
def view_score(score_id):
    if isinstance(current_user, Admin):
        return redirect(url_for('admin_dashboard'))
        
    score = Score.query.get_or_404(score_id)
    
    # Ensure the score belongs to the current user
    if score.user_id != current_user.id:
        flash('You do not have permission to view this score.', 'danger')
        return redirect(url_for('user_dashboard'))
    
    quiz = Quiz.query.get_or_404(score.quiz_id)
    questions = Question.query.filter_by(quiz_id=score.quiz_id).all()
    
    # Retrieve previous quiz results from session if available, otherwise show blank
    user_answers = session.get(f'quiz_{score.quiz_id}_answers', {})
    
    # Calculate stats
    correct_answers = 0
    wrong_answers = 0
    not_attempted = len(questions)
    
    for question in questions:
        if question.id in user_answers:
            not_attempted -= 1
            if user_answers[question.id] == question.correct_option:
                correct_answers += 1
            else:
                wrong_answers += 1
    
    # Calculate accuracy
    attempted = correct_answers + wrong_answers
    accuracy = 0
    if attempted > 0:
        accuracy = round((correct_answers / attempted) * 100)
    
    # Get progress data for chart
    recent_scores = Score.query.filter_by(user_id=current_user.id).order_by(Score.time_stamp_of_attempt.desc()).limit(5).all()
    recent_scores.reverse()  # Show oldest to newest for progression
    
    progress_labels = [s.time_stamp_of_attempt.strftime('%d/%m/%Y') for s in recent_scores]
    progress_data = [s.total_scored for s in recent_scores]
    
    return render_template('user/results.html', 
                          quiz=quiz,
                          score=score,
                          correct_answers=correct_answers,
                          wrong_answers=wrong_answers,
                          not_attempted=not_attempted,
                          total_questions=len(questions),
                          accuracy=accuracy,
                          progress_labels=progress_labels,
                          progress_data=progress_data)

@app.route('/quiz/<int:quiz_id>/review/<int:score_id>')
@login_required
def review_quiz(quiz_id, score_id):
    if isinstance(current_user, Admin):
        return redirect(url_for('admin_dashboard'))
        
    quiz = Quiz.query.get_or_404(quiz_id)
    score = Score.query.get_or_404(score_id)
    
    # Ensure the score belongs to the current user
    if score.user_id != current_user.id:
        flash('You do not have permission to view this score.', 'danger')
        return redirect(url_for('user_dashboard'))
    
    questions = Question.query.filter_by(quiz_id=quiz_id).all()
    
    # Retrieve user's answers from session
    user_answers = session.get(f'quiz_{quiz_id}_answers', {})
    
    # Calculate stats
    correct_answers = 0
    wrong_answers = 0
    not_attempted = len(questions)
    
    for question in questions:
        if question.id in user_answers:
            not_attempted -= 1
            if user_answers[question.id] == question.correct_option:
                correct_answers += 1
            else:
                wrong_answers += 1
    
    return render_template('user/review_quiz.html',
                          quiz=quiz,
                          score=score,
                          questions=questions,
                          user_answers=user_answers,
                          correct_answers=correct_answers,
                          wrong_answers=wrong_answers,
                          not_attempted=not_attempted)

# Initialize database
with app.app_context():
    db.create_all()
    # Create admin if not exists
    if not Admin.query.filter_by(username='admin').first():
        admin = Admin(
            username='admin',
            password=generate_password_hash('admin@123')
        )
        db.session.add(admin)
        db.session.commit()
        logging.info("Admin account created")