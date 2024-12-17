import os
import qrcode
from PIL import Image
from pyzbar.pyzbar import decode
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from datetime import date

app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///bus_tracking.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'uploads'
db = SQLAlchemy(app)

# Database Models
class Ticket(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(50), nullable=False)
    from_location = db.Column(db.String(50), nullable=False)
    to_location = db.Column(db.String(50), nullable=False)
    fare = db.Column(db.Float, nullable=False)
    ticket_date = db.Column(db.Date, default=date.today)
    is_validated = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    qr_code_path = db.Column(db.String(200))

class DailyPassengerCount(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, unique=True, nullable=False)
    total_passengers = db.Column(db.Integer, default=0)
    current_passengers = db.Column(db.Integer, default=0)

# Dummy credentials (replace with more secure authentication in production)
VALID_USER = {
    "user": "password",
    "user2": "123"
}
VALID_MANAGER = {"manager": "admin123"}

# Fare configuration
FARES = {
    ("City A", "City B"): 50,
    ("City A", "City C"): 80,
    ("City B", "City C"): 30
}

def generate_qr_code(ticket):
    """Generate QR code for a ticket"""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr_data = f"Ticket ID: {ticket.id}\n" \
              f"From: {ticket.from_location}\n" \
              f"To: {ticket.to_location}\n" \
              f"Fare: ${ticket.fare}\n" \
              f"Date: {ticket.ticket_date}"
    
    qr.add_data(qr_data)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    
    # Ensure uploads directory exists
    os.makedirs('uploads', exist_ok=True)
    qr_path = f'uploads/ticket_{ticket.id}_qr.png'
    img.save(qr_path)
    return qr_path

@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        # User login
        if username in VALID_USER and VALID_USER[username] == password:
            session['user'] = username
            return redirect(url_for('dashboard'))
        
        # Manager login
        elif username in VALID_MANAGER and VALID_MANAGER[username] == password:
            session['manager'] = username
            return redirect(url_for('manager_dashboard'))
        
        else:
            flash('Invalid credentials', 'error')
    
    return render_template('login.html')

@app.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
    if 'user' not in session:
        return redirect(url_for('login'))
    
    fare = None
    if request.method == 'POST':
        from_location = request.form.get('from')
        to_location = request.form.get('to')
        fare = FARES.get((from_location, to_location), None)
        
        if fare is not None:
            session['from'] = from_location
            session['to'] = to_location
            session['fare'] = fare
            return redirect(url_for('book_ticket'))
        else:
            flash('Invalid route selected', 'error')
    
    return render_template('dashboard.html')

@app.route('/book_ticket', methods=['GET', 'POST'])
def book_ticket():
    if 'user' not in session or 'fare' not in session:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        # Create new ticket
        new_ticket = Ticket(
            user_id=session['user'],
            from_location=session['from'],
            to_location=session['to'],
            fare=session['fare'],
            ticket_date=date.today()
        )
        
        # Save ticket to database
        db.session.add(new_ticket)
        db.session.commit()
        
        # Generate QR code
        qr_path = generate_qr_code(new_ticket)
        new_ticket.qr_code_path = qr_path
        db.session.commit()
        
        # Update daily passenger count
        today = date.today()
        daily_count = DailyPassengerCount.query.filter_by(date=today).first()
        if not daily_count:
            daily_count = DailyPassengerCount(date=today, total_passengers=1, current_passengers=1)
            db.session.add(daily_count)
        else:
            daily_count.total_passengers += 1
            daily_count.current_passengers += 1
        db.session.commit()
        
        # Clear session data
        session.pop('from', None)
        session.pop('to', None)
        session.pop('fare', None)
        
        flash(f'Ticket booked successfully! Ticket ID: {new_ticket.id}', 'success')
        return redirect(url_for('view_ticket', ticket_id=new_ticket.id))
    
    return render_template('book_ticket.html', 
                           fare=session['fare'], 
                           from_location=session['from'], 
                           to_location=session['to'])

@app.route('/view_ticket/<int:ticket_id>')
def view_ticket(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    return render_template('view_ticket.html', ticket=ticket)

@app.route('/manager_dashboard', methods=['GET', 'POST'])
def manager_dashboard():
    if 'manager' not in session:
        return redirect(url_for('login'))
    
    today = date.today()
    daily_count = DailyPassengerCount.query.filter_by(date=today).first()
    
    if request.method == 'POST':
        # Check if a file was uploaded for QR code scanning
        if 'qr_image' in request.files:
            qr_image = request.files['qr_image']
            
            if qr_image:
                # Save the uploaded image temporarily
                image_path = os.path.join(app.config['UPLOAD_FOLDER'], 'temp_qr.png')
                qr_image.save(image_path)
                
                try:
                    # Decode the QR code
                    decoded_objs = decode(Image.open(image_path))
                    
                    if decoded_objs:
                        # Extract ticket information from QR code
                        qr_data = decoded_objs[0].data.decode('utf-8')
                        
                        # Parse ticket ID from QR code data
                        ticket_id = None
                        for line in qr_data.split('\n'):
                            if line.startswith('Ticket ID:'):
                                ticket_id = line.split(':')[1].strip()
                                break
                        
                        if ticket_id:
                            # Find and validate the ticket
                            ticket = Ticket.query.get(int(ticket_id))
                            
                            if ticket:
                                if not ticket.is_validated:
                                    ticket.is_validated = True
                                    
                                    # Decrement current passenger count
                                    if daily_count:
                                        daily_count.current_passengers = max(0, daily_count.current_passengers+0)
                                    
                                    db.session.commit()
                                    flash(f'Ticket {ticket_id} validated successfully!', 'success')
                                else:
                                    flash(f'Ticket {ticket_id} already validated', 'warning')
                            else:
                                flash('Invalid ticket', 'error')
                        else:
                            flash('Could not extract ticket information', 'error')
                    else:
                        flash('No QR code found in the image', 'error')
                
                except Exception as e:
                    flash(f'Error processing QR code: {str(e)}', 'error')
                
                # Remove temporary image
                os.remove(image_path)
        
        # Manual ticket ID validation (keep previous logic)
        elif request.form.get('ticket_id'):
            ticket_id = request.form.get('ticket_id')
            ticket = Ticket.query.get(ticket_id)
            
            if ticket:
                if not ticket.is_validated:
                    ticket.is_validated = True
                    
                    # Decrement current passenger count
                    if daily_count:
                        daily_count.current_passengers = max(0, daily_count.current_passengers - 1)
                    
                    db.session.commit()
                    flash('Ticket validated successfully!', 'success')
                else:
                    flash('Ticket already validated', 'warning')
            else:
                flash('Invalid ticket', 'error')
    
    return render_template('manager_dashboard.html', 
                           daily_count=daily_count)

@app.route('/qr_code/<int:ticket_id>')
def qr_code(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    return send_file(ticket.qr_code_path, mimetype='image/png')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# Initialize database
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True)