let timeLeft = 0;
let timerId = null;

function startTimer(duration) {
    timeLeft = duration * 60;
    updateTimer();
    timerId = setInterval(updateTimer, 1000);
}

function updateTimer() {
    if (timeLeft <= 0) {
        clearInterval(timerId);
        document.getElementById('quiz-form').submit();
        return;
    }
    
    const minutes = Math.floor(timeLeft / 60);
    const seconds = timeLeft % 60;
    document.getElementById('timer').textContent = 
        `${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
    timeLeft--;
}

function submitQuiz() {
    clearInterval(timerId);
    document.getElementById('quiz-form').submit();
}
