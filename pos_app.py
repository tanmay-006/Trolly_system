#!/usr/bin/env python3
"""
Point of Sale System with Product Scanning and QR Code Payments
Complete POS flow: Scan → Cart → Total → QR Code → Receipt
"""

from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
import razorpay
import json
import os
import qrcode
import io
import base64
from datetime import datetime
import uuid
import logging

app = Flask(__name__)
app.secret_key = 'pos-system-secret-key'

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Razorpay client
def get_razorpay_client():
    """Initialize Razorpay client with real test credentials"""
    API_KEY = "rzp_test_SDiqKHyr1tduXG"
    API_SECRET = "5pFqUdxICfF7FrxPTj6Nuu7c"
    
    client = razorpay.Client(auth=(API_KEY, API_SECRET))
    client.enable_retry(True)
    client.set_app_details({
        "title": "POS Payment System",
        "version": "1.0.0"
    })
    return client

razorpay_client = get_razorpay_client()

# Product Database with weight information
PRODUCTS = {
    "1001": {"name": "Laptop", "price": 45000, "category": "Electronics", "expected_weight": 1500, "tolerance": 50},
    "1002": {"name": "Mouse", "price": 800, "category": "Electronics", "expected_weight": 120, "tolerance": 20},
    "1003": {"name": "Keyboard", "price": 1500, "category": "Electronics", "expected_weight": 800, "tolerance": 30},
    "1004": {"name": "Monitor", "price": 12000, "category": "Electronics", "expected_weight": 3000, "tolerance": 100},
    "1005": {"name": "Headphones", "price": 2000, "category": "Electronics", "expected_weight": 400, "tolerance": 25},
    "1006": {"name": "USB Cable", "price": 200, "category": "Accessories", "expected_weight": 50, "tolerance": 10},
    "1007": {"name": "Webcam", "price": 2500, "category": "Electronics", "expected_weight": 200, "tolerance": 15},
    "1008": {"name": "Phone Case", "price": 500, "category": "Accessories", "expected_weight": 80, "tolerance": 12},
    "1009": {"name": "Power Bank", "price": 1500, "category": "Electronics", "expected_weight": 300, "tolerance": 20},
    "1010": {"name": "Bluetooth Speaker", "price": 3000, "category": "Electronics", "expected_weight": 600, "tolerance": 30}
}

# Shopping cart (in-memory for demo)
shopping_cart = []

@app.route('/')
def pos_dashboard():
    """Main POS dashboard"""
    return render_template('pos_dashboard.html', products=PRODUCTS)

@app.route('/api/scan_product', methods=['POST'])
def scan_product():
    """Scan product by barcode/product ID"""
    try:
        data = request.get_json()
        product_id = data.get('product_id')
        
        if product_id not in PRODUCTS:
            return jsonify({
                'success': False,
                'error': 'Product not found',
                'message': f'Product with ID {product_id} not found'
            }), 404
        
        product = PRODUCTS[product_id].copy()
        product['id'] = product_id
        product['quantity'] = 1
        product['total'] = product['price']
        
        return jsonify({
            'success': True,
            'product': product,
            'message': f'Product {product["name"]} scanned successfully'
        })
        
    except Exception as e:
        logger.error(f"Error scanning product: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e),
            'message': 'Failed to scan product'
        }), 400

@app.route('/api/add_to_cart', methods=['POST'])
def add_to_cart():
    """Add product to shopping cart"""
    try:
        global shopping_cart
        data = request.get_json()
        product_id = data.get('product_id')
        quantity = data.get('quantity', 1)
        
        if product_id not in PRODUCTS:
            return jsonify({
                'success': False,
                'error': 'Product not found'
            }), 404
        
        # Check if product already in cart
        for item in shopping_cart:
            if item['id'] == product_id:
                item['quantity'] += quantity
                item['total'] = item['price'] * item['quantity']
                break
        else:
            product = PRODUCTS[product_id].copy()
            product['id'] = product_id
            product['quantity'] = quantity
            product['total'] = product['price'] * quantity
            shopping_cart.append(product)
        
        return jsonify({
            'success': True,
            'cart': shopping_cart,
            'cart_count': len(shopping_cart),
            'cart_total': calculate_cart_total()
        })
        
    except Exception as e:
        logger.error(f"Error adding to cart: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 400

@app.route('/api/get_cart')
def get_cart():
    """Get current shopping cart"""
    return jsonify({
        'success': True,
        'cart': shopping_cart,
        'cart_count': len(shopping_cart),
        'cart_total': calculate_cart_total()
    })

@app.route('/api/remove_from_cart', methods=['POST'])
def remove_from_cart():
    """Remove item from cart"""
    try:
        global shopping_cart
        data = request.get_json()
        product_id = data.get('product_id')
        
        shopping_cart = [item for item in shopping_cart if item['id'] != product_id]
        
        return jsonify({
            'success': True,
            'cart': shopping_cart,
            'cart_count': len(shopping_cart),
            'cart_total': calculate_cart_total()
        })
        
    except Exception as e:
        logger.error(f"Error removing from cart: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 400

@app.route('/api/clear_cart', methods=['POST'])
def clear_cart():
    """Clear entire cart"""
    global shopping_cart
    shopping_cart = []
    
    return jsonify({
        'success': True,
        'cart': [],
        'cart_count': 0,
        'cart_total': 0
    })

def calculate_cart_total():
    """Calculate total cart amount"""
    return sum(item['total'] for item in shopping_cart)

@app.route('/api/create_payment_order', methods=['POST'])
def create_payment_order():
    """Create payment order and generate QR code"""
    try:
        if not shopping_cart:
            return jsonify({
                'success': False,
                'error': 'Cart is empty'
            }), 400
        
        cart_total = calculate_cart_total()
        
        # Calculate final amount with GST (18%)
        gst_amount = cart_total * 0.18
        final_total = cart_total + gst_amount
        
        # Create Razorpay order with final total including GST
        order_data = {
            'amount': int(final_total * 100),  # Convert to paise
            'currency': 'INR',
            'receipt': f'pos_receipt_{datetime.now().strftime("%Y%m%d_%H%M%S")}',
            'payment_capture': 1
        }
        
        order = razorpay_client.order.create(data=order_data)
        
        # Generate QR code for payment with valid UPI ID
        # Using valid test UPI ID format
        merchant_upi_id = "test@ybl"  # Yes Bank test UPI ID (commonly used)
        merchant_name = "Razorpay POS"
        transaction_note = order['id']
        
        # Create UPI payment URL with proper format and final total including GST
        qr_data = f"upi://pay?pa={merchant_upi_id}&pn={merchant_name}&am={final_total:.2f}&cu=INR&tn={transaction_note}&mc=5411&tr={order['id']}&url=https://razorpay.com"
        
        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(qr_data)
        qr.make(fit=True)
        
        qr_image = qr.make_image(fill_color="black", back_color="white")
        
        # Convert to base64 for web display
        img_buffer = io.BytesIO()
        qr_image.save(img_buffer, format='PNG')
        qr_base64 = base64.b64encode(img_buffer.getvalue()).decode()
        
        return jsonify({
            'success': True,
            'order': order,
            'qr_code': f"data:image/png;base64,{qr_base64}",
            'cart_total': cart_total,
            'gst_amount': gst_amount,
            'final_total': final_total,
            'cart_items': shopping_cart,
            'message': 'Payment QR code generated successfully'
        })
        
    except Exception as e:
        logger.error(f"Error creating payment order: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e),
            'message': 'Failed to create payment order'
        }), 400

@app.route('/api/complete_payment', methods=['POST'])
def complete_payment():
    """Complete payment and generate receipt"""
    try:
        global shopping_cart
        data = request.get_json()
        order_id = data.get('order_id')
        payment_id = data.get('payment_id', f'pay_demo_{uuid.uuid4().hex[:8]}')
        
        if not shopping_cart:
            return jsonify({
                'success': False,
                'error': 'Cart is empty'
            }), 400
        
        cart_total = calculate_cart_total()
        
        # Generate receipt
        receipt = {
            'receipt_id': f'RECEIPT_{datetime.now().strftime("%Y%m%d_%H%M%S")}',
            'order_id': order_id,
            'payment_id': payment_id,
            'date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'items': shopping_cart.copy(),
            'subtotal': cart_total,
            'tax': cart_total * 0.18,  # 18% GST
            'total': cart_total * 1.18,
            'payment_method': 'UPI/QR Code',
            'status': 'PAID'
        }
        
        # Clear cart after successful payment
        shopping_cart = []
        
        return jsonify({
            'success': True,
            'receipt': receipt,
            'message': 'Payment completed successfully'
        })
        
    except Exception as e:
        logger.error(f"Error completing payment: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e),
            'message': 'Failed to complete payment'
        }), 400

@app.route('/receipt/<receipt_id>')
def view_receipt(receipt_id):
    """View payment receipt"""
    return render_template('receipt.html', receipt_id=receipt_id)

@app.route('/health')
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'service': 'POS Payment System'
    })

if __name__ == '__main__':
    # Create templates directory if it doesn't exist
    if not os.path.exists('templates'):
        os.makedirs('templates')
    
    # Create static directory if it doesn't exist
    if not os.path.exists('static'):
        os.makedirs('static')
    
    print("Starting POS Payment System...")
    print(f"POS Dashboard: http://10.115.140.72:5000")
    print(f"Health Check: http://10.115.140.72:5000/health")
    app.run(host='0.0.0.0', port=5000, debug=True)
