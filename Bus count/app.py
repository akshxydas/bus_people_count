import os
import qrcode
from PIL import Image
from pyzbar.pyzbar import decode
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import date
from geopy.distance import geodesic


app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///bus_tracking.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'uploads'
db = SQLAlchemy(app)

# target_location = (9.9312, 76.2673)  # Example: Cochin, India

# Threshold distance in meters
# threshold = 10

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

    destination_latitude = db.Column(db.Float)
    destination_longitude = db.Column(db.Float)
    current_latitude = db.Column(db.Float)
    current_longitude = db.Column(db.Float)
    is_arrived = db.Column(db.Boolean, default=False)

    
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

CITY_COORDINATES = {
    "City A": (13.0827, 80.2707),  # Chennai coordinates
    "City B": (12.9716, 77.5946),  # Bangalore coordinates
    "City C": (19.0760, 72.8777)   # Mumbai coordinates
}
# Fare configuration
FARES = {
    ("City A", "City B"): 50,
    ("City A", "City C"): 80,
    ("City B", "City C"): 30
}
@app.route('/update_location', methods=['POST'])
def update_location():
    if 'user' not in session:
        return jsonify({"error": "Unauthorized"}), 401
    
    data = request.get_json()
    current_lat = data.get('latitude')
    current_lon = data.get('longitude')
    
    if not current_lat or not current_lon:
        return jsonify({"error": "Invalid coordinates"}), 400
    
    # Find the active ticket for the current user
    ticket = Ticket.query.filter_by(
        user_id=session['user'], 
        is_active=True, 
        is_arrived=False
    ).first()
    
    if not ticket:
        return jsonify({"error": "No active ticket found"}), 404
    
    # Update current location
    ticket.current_latitude = current_lat
    ticket.current_longitude = current_lon
    
    # Get destination coordinates
    destination_coords = CITY_COORDINATES.get(ticket.to_location)
    
    if not destination_coords:
        return jsonify({"error": "Destination not found"}), 404
    
    # Check if user is close to destination
    current_location = (current_lat, current_lon)
    destination_location = destination_coords
    
    # Calculate distance (in kilometers)
    distance = geodesic(current_location, destination_location).kilometers
    
    # Proximity threshold (e.g., 1 kilometer)
    PROXIMITY_THRESHOLD = 1.0
    
    if distance <= PROXIMITY_THRESHOLD:
        # Decrement passenger count
        today = date.today()
        daily_count = DailyPassengerCount.query.filter_by(date=today).first()
        
        if daily_count and daily_count.current_passengers > 0:
            daily_count.current_passengers -= 1
            ticket.is_arrived = True
            ticket.is_active = False
        
        db.session.commit()
        
        return jsonify({
            "status": "arrived", 
            "distance": distance,
            "message": "Destination reached. Passenger count updated."
        }), 200
    
    # Save the current location
    db.session.commit()
    
    return jsonify({
        "status": "tracking", 
        "distance": distance,
        "message": "Location updated"
    }), 200

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
        # Get destination coordinates
        destination_coords = CITY_COORDINATES.get(session['to'])
        
        if not destination_coords:
            flash('Invalid destination', 'error')
            return redirect(url_for('dashboard'))
        
        # Create new ticket with destination coordinates
        new_ticket = Ticket(
            user_id=session['user'],
            from_location=session['from'],
            to_location=session['to'],
            fare=session['fare'],
            ticket_date=date.today(),
            destination_latitude=destination_coords[0],
            destination_longitude=destination_coords[1]
        )
        
        db.session.add(new_ticket)
        db.session.commit()
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
        return redirect(url_for('track_journey', ticket_id=new_ticket.id))
    return render_template('book_ticket.html', 
                           fare=session['fare'], 
                           from_location=session['from'], 
                           to_location=session['to'])

@app.route('/track_journey/<int:ticket_id>')
def track_journey(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    
    return render_template('track_journey.html', ticket=ticket)

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

@app.route('/clear_database', methods=['GET', 'POST'])
def clear_database():
    if 'manager' not in session:
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        confirmation = request.form.get('confirmation', '').upper()
        
        if confirmation != 'CONFIRM':
            flash('Database clear cancelled. Incorrect confirmation.', 'warning')
            return redirect(url_for('manager_dashboard'))
        
        try:
            # Clear all tables
            Ticket.query.delete()
            DailyPassengerCount.query.delete()
            
            # Commit the changes
            db.session.commit()
            
            # Remove uploaded QR code files
            import os
            qr_folder = app.config.get('UPLOAD_FOLDER', 'uploads')
            
            # Ensure the folder exists before trying to list or delete contents
            if os.path.exists(qr_folder):
                for filename in os.listdir(qr_folder):
                    file_path = os.path.join(qr_folder, filename)
                    try:
                        if os.path.isfile(file_path):
                            os.unlink(file_path)
                    except Exception as e:
                        app.logger.error(f"Error deleting file {file_path}: {e}")
            
            flash('Database cleared successfully!', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Error clearing database: {str(e)}', 'danger')
        
        return redirect(url_for('manager_dashboard'))
    
    # GET request renders the confirmation page
    return render_template('confirm_clear_database.html')


# Initialize database
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True)