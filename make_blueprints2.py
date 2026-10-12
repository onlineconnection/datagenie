import json, os

E = ["description!", "amount:numeric!", "expense_date:date!"]
CU = ["name!", "phone"]

# slug: (title, icon, category, description, keywords, tables)
B = {
"hotel": ("Hotel & Guest House", "🏨", "Hospitality", "Rooms, guests, bookings, payments and extra services.",
  ["hotel", "guest house", "lodge", "rooms", "booking", "accommodation"], {
  "rooms": ["room_number!", "room_type", "price_per_night:numeric!", "status"],
  "guests": ["full_name!", "phone", "email", "id_number"],
  "bookings": ["guest_id:integer!>guests.id", "room_id:integer!>rooms.id", "check_in_date:date!", "check_out_date:date!", "total_amount:numeric!", "status"],
  "payments": ["booking_id:integer!>bookings.id", "amount:numeric!", "payment_date:date!", "method"],
  "services": ["booking_id:integer>bookings.id", "description!", "amount:numeric!", "service_date:date"],
  "staff": ["name!", "role", "phone", "salary:numeric"],
  "expenses": E}),
"salon": ("Salon & Barbershop", "💇", "Services", "Clients, stylists, appointments, services and product sales.",
  ["salon", "barber", "barbershop", "hair", "beauty", "braids", "nails"], {
  "services": ["name!", "price:numeric!", "duration_minutes:integer"],
  "clients": ["name!", "phone", "notes"],
  "stylists": ["name!", "phone", "commission_percent:numeric"],
  "appointments": ["client_id:integer!>clients.id", "stylist_id:integer>stylists.id", "service_id:integer!>services.id", "appointment_time:timestamp!", "status", "price:numeric"],
  "products": ["name!", "cost_price:numeric", "selling_price:numeric", "stock_quantity:integer!"],
  "expenses": E}),
"gym": ("Gym & Fitness Club", "🏋️", "Services", "Members, plans, payments, trainers, classes and attendance.",
  ["gym", "fitness", "training", "membership", "workout", "sports club"], {
  "membership_plans": ["name!", "price:numeric!", "duration_days:integer!"],
  "members": ["full_name!", "phone", "email", "join_date:date!", "plan_id:integer>membership_plans.id", "status"],
  "payments": ["member_id:integer!>members.id", "amount:numeric!", "payment_date:date!", "method"],
  "trainers": ["name!", "phone", "specialty"],
  "classes": ["name!", "trainer_id:integer>trainers.id", "schedule", "capacity:integer"],
  "attendance": ["member_id:integer!>members.id", "visit_date:date!"],
  "equipment": ["name!", "quantity:integer", "condition", "purchase_date:date"]}),
"clinic": ("Clinic & Health Center", "🩺", "Health", "Patients, doctors, visits, prescriptions, lab tests and fees.",
  ["clinic", "health center", "hospital", "patients", "doctor", "nurse", "medical"], {
  "patients": ["full_name!", "gender", "birth_date:date", "phone", "address"],
  "doctors": ["name!", "specialty", "phone"],
  "visits": ["patient_id:integer!>patients.id", "doctor_id:integer>doctors.id", "visit_date:date!", "complaint", "diagnosis", "fee:numeric"],
  "prescriptions": ["visit_id:integer!>visits.id", "medicine!", "dosage", "days:integer"],
  "lab_tests": ["visit_id:integer!>visits.id", "test_name!", "result", "cost:numeric"],
  "payments": ["patient_id:integer!>patients.id", "amount:numeric!", "payment_date:date!"],
  "supplies": ["item_name!", "quantity:integer!", "reorder_level:integer"]}),
"hardware": ("Hardware & Building Supplies", "🔧", "Retail", "Products, suppliers, purchases, customer credit and sales.",
  ["hardware", "building materials", "cement", "tools", "plumbing", "construction supplies"], {
  "categories": ["name!"],
  "suppliers": CU,
  "products": ["name!", "category_id:integer>categories.id", "supplier_id:integer>suppliers.id", "cost_price:numeric!", "selling_price:numeric!", "stock_quantity:integer!", "reorder_level:integer"],
  "purchases": ["supplier_id:integer!>suppliers.id", "product_id:integer!>products.id", "quantity:integer!", "unit_cost:numeric!", "purchase_date:date!"],
  "customers": ["name!", "phone", "credit_balance:numeric"],
  "sales": ["customer_id:integer>customers.id", "sale_date:date!", "total_amount:numeric!", "payment_status"],
  "sale_items": ["sale_id:integer!>sales.id", "product_id:integer!>products.id", "quantity:integer!", "unit_price:numeric!"],
  "expenses": E}),
"transport": ("Taxi & Transport", "🚕", "Transport", "Vehicles, drivers, trips, fuel, maintenance and daily remittance.",
  ["taxi", "transport", "keh keh", "motorbike", "bus", "driver", "vehicles", "okada"], {
  "vehicles": ["plate_number!", "vehicle_type", "model", "status"],
  "drivers": ["name!", "phone", "license_number", "vehicle_id:integer>vehicles.id"],
  "trips": ["driver_id:integer!>drivers.id", "trip_date:date!", "route", "fare:numeric!"],
  "fuel_logs": ["vehicle_id:integer!>vehicles.id", "fuel_date:date!", "liters:numeric", "cost:numeric!"],
  "maintenance": ["vehicle_id:integer!>vehicles.id", "description!", "cost:numeric!", "service_date:date!"],
  "daily_remittance": ["driver_id:integer!>drivers.id", "remit_date:date!", "amount_due:numeric!", "amount_paid:numeric!"],
  "expenses": E}),
"real_estate": ("Real Estate & Rentals", "🏘️", "Property", "Properties, tenants, leases, rent payments and repairs.",
  ["real estate", "rental", "landlord", "tenant", "property", "apartment", "houses", "rent"], {
  "owners": ["name!", "phone", "email"],
  "properties": ["title!", "address", "property_type", "owner_id:integer>owners.id", "monthly_rent:numeric", "status"],
  "tenants": ["full_name!", "phone", "email", "id_number"],
  "leases": ["property_id:integer!>properties.id", "tenant_id:integer!>tenants.id", "start_date:date!", "end_date:date", "monthly_rent:numeric!"],
  "rent_payments": ["lease_id:integer!>leases.id", "amount:numeric!", "payment_date:date!", "month_covered"],
  "repairs": ["property_id:integer!>properties.id", "description!", "cost:numeric", "request_date:date", "status"],
  "expenses": E}),
"church": ("Church & NGO", "⛪", "Community", "Members, donations, pledges, events and ministries.",
  ["church", "ngo", "donations", "congregation", "charity", "nonprofit", "ministry", "community"], {
  "members": ["full_name!", "phone", "gender", "join_date:date", "address"],
  "ministries": ["name!", "leader_name", "phone"],
  "donations": ["member_id:integer>members.id", "amount:numeric!", "donation_date:date!", "purpose"],
  "pledges": ["member_id:integer!>members.id", "amount:numeric!", "due_date:date", "paid:boolean"],
  "events": ["title!", "event_date:date!", "location", "budget:numeric"],
  "event_attendance": ["event_id:integer!>events.id", "member_id:integer!>members.id"],
  "expenses": E}),
"library": ("Library & Bookshop", "📚", "Education", "Books, members, loans, fines and purchases.",
  ["library", "books", "bookshop", "reading", "borrow", "loans of books"], {
  "books": ["title!", "author", "isbn", "category", "copies:integer!"],
  "members": ["full_name!", "phone", "email", "join_date:date"],
  "loans": ["book_id:integer!>books.id", "member_id:integer!>members.id", "loan_date:date!", "due_date:date!", "returned_date:date"],
  "fines": ["loan_id:integer!>loans.id", "amount:numeric!", "paid:boolean"],
  "purchases": ["book_id:integer>books.id", "quantity:integer", "cost:numeric", "purchase_date:date"],
  "staff": ["name!", "role", "phone"]}),
"boutique": ("Clothing & Fashion Boutique", "👗", "Retail", "Stock by size and color, customers, sales and suppliers.",
  ["boutique", "clothing", "fashion", "clothes", "shoes", "tailor", "textile"], {
  "categories": ["name!"],
  "suppliers": CU,
  "products": ["name!", "category_id:integer>categories.id", "supplier_id:integer>suppliers.id", "size", "color", "cost_price:numeric!", "selling_price:numeric!", "stock_quantity:integer!"],
  "customers": CU,
  "sales": ["customer_id:integer>customers.id", "sale_date:date!", "total_amount:numeric!", "payment_method"],
  "sale_items": ["sale_id:integer!>sales.id", "product_id:integer!>products.id", "quantity:integer!", "unit_price:numeric!"],
  "expenses": E}),
"mobile_money": ("Mobile Money & Airtime", "📱", "Finance", "Transactions, float, airtime sales and daily cash closing.",
  ["mobile money", "airtime", "momo", "orange money", "lonestar", "float", "agent"], {
  "customers": CU,
  "transactions": ["transaction_time:timestamp!", "transaction_kind!", "customer_id:integer>customers.id", "amount:numeric!", "commission:numeric", "reference"],
  "float_balances": ["provider!", "balance_date:date!", "cash_balance:numeric", "float_balance:numeric"],
  "airtime_sales": ["network!", "amount:numeric!", "cost:numeric", "sale_date:date!"],
  "daily_closing": ["closing_date:date!", "opening_cash:numeric", "closing_cash:numeric", "notes"],
  "expenses": E}),
"wholesale": ("Wholesale & Distribution", "📦", "Retail", "Suppliers, stock, retailer orders, deliveries and payments.",
  ["wholesale", "distribution", "distributor", "retailers", "bulk", "warehouse"], {
  "suppliers": CU,
  "products": ["name!", "supplier_id:integer>suppliers.id", "unit", "cost_price:numeric!", "selling_price:numeric!", "stock_quantity:integer!"],
  "purchases": ["supplier_id:integer!>suppliers.id", "product_id:integer!>products.id", "quantity:integer!", "unit_cost:numeric!", "purchase_date:date!"],
  "retailers": ["name!", "phone", "address", "credit_balance:numeric"],
  "orders": ["retailer_id:integer!>retailers.id", "order_date:date!", "status", "total_amount:numeric!"],
  "order_items": ["order_id:integer!>orders.id", "product_id:integer!>products.id", "quantity:integer!", "unit_price:numeric!"],
  "deliveries": ["order_id:integer!>orders.id", "delivery_date:date", "driver_name", "status"],
  "payments": ["order_id:integer!>orders.id", "amount:numeric!", "payment_date:date!"]}),
"construction": ("Construction Projects", "🏗️", "Construction", "Projects, workers, materials, timesheets and client payments.",
  ["construction", "builder", "contractor", "building project", "site", "masonry", "carpenter"], {
  "projects": ["name!", "client_name", "location", "start_date:date", "end_date:date", "budget:numeric", "status"],
  "workers": ["name!", "trade", "phone", "daily_wage:numeric"],
  "materials": ["name!", "unit", "unit_cost:numeric"],
  "material_usage": ["project_id:integer!>projects.id", "material_id:integer!>materials.id", "quantity:numeric!", "usage_date:date"],
  "timesheets": ["project_id:integer!>projects.id", "worker_id:integer!>workers.id", "work_date:date!", "days_worked:numeric!"],
  "payments_received": ["project_id:integer!>projects.id", "amount:numeric!", "payment_date:date!"],
  "expenses": ["project_id:integer>projects.id", "description!", "amount:numeric!", "expense_date:date!"]}),
"auto_repair": ("Garage & Auto Repair", "🚗", "Services", "Customers, vehicles, job cards, parts and payments.",
  ["garage", "auto repair", "mechanic", "car repair", "spare parts", "workshop"], {
  "customers": CU,
  "vehicles": ["customer_id:integer!>customers.id", "plate_number!", "make", "model", "year:integer"],
  "mechanics": ["name!", "phone", "specialty"],
  "job_cards": ["vehicle_id:integer!>vehicles.id", "mechanic_id:integer>mechanics.id", "job_date:date!", "problem!", "status", "labor_cost:numeric"],
  "parts": ["name!", "cost_price:numeric", "selling_price:numeric", "stock_quantity:integer!"],
  "job_parts": ["job_card_id:integer!>job_cards.id", "part_id:integer!>parts.id", "quantity:integer!", "unit_price:numeric!"],
  "payments": ["job_card_id:integer!>job_cards.id", "amount:numeric!", "payment_date:date!"]}),
"events": ("Event Planning", "🎉", "Services", "Clients, events, vendors, guest lists, tasks and payments.",
  ["event", "events", "wedding", "party", "planner", "catering", "decoration", "mc"], {
  "clients": ["name!", "phone", "email"],
  "events": ["title!", "client_id:integer>clients.id", "event_date:date!", "venue", "budget:numeric", "status"],
  "vendors": ["name!", "service", "phone"],
  "event_vendors": ["event_id:integer!>events.id", "vendor_id:integer!>vendors.id", "cost:numeric!"],
  "guests": ["event_id:integer!>events.id", "name!", "phone", "rsvp:boolean"],
  "event_tasks": ["event_id:integer!>events.id", "task!", "due_date:date", "done:boolean"],
  "payments": ["event_id:integer!>events.id", "amount:numeric!", "payment_date:date!"]}),
"poultry": ("Poultry & Egg Farm", "🐔", "Agriculture", "Flocks, eggs, feed, vaccinations, losses and sales.",
  ["poultry", "chicken", "eggs", "layers", "broilers", "hatchery", "livestock farm"], {
  "flocks": ["name!", "bird_type", "start_date:date", "initial_count:integer!", "status"],
  "egg_production": ["flock_id:integer!>flocks.id", "collection_date:date!", "eggs_collected:integer!", "broken:integer"],
  "feed_purchases": ["flock_id:integer>flocks.id", "feed_type", "quantity_kg:numeric", "cost:numeric!", "purchase_date:date!"],
  "mortality": ["flock_id:integer!>flocks.id", "record_date:date!", "deaths:integer!", "cause"],
  "vaccinations": ["flock_id:integer!>flocks.id", "vaccine!", "vaccination_date:date!", "cost:numeric"],
  "buyers": CU,
  "sales": ["buyer_id:integer>buyers.id", "flock_id:integer>flocks.id", "item", "quantity:integer!", "unit_price:numeric!", "total_amount:numeric!", "sale_date:date!"]}),
"fishing": ("Fishing Business", "🐟", "Agriculture", "Boats, crew, trips, catches, buyers and sales.",
  ["fishing", "fish", "boat", "catch", "fishermen", "seafood", "canoe"], {
  "boats": ["name!", "owner_name", "capacity_kg:numeric", "status"],
  "crew": ["name!", "phone", "role", "share_percent:numeric"],
  "trips": ["boat_id:integer!>boats.id", "trip_date:date!", "fuel_cost:numeric", "other_cost:numeric", "notes"],
  "catches": ["trip_id:integer!>trips.id", "fish_type!", "weight_kg:numeric!", "price_per_kg:numeric"],
  "buyers": CU,
  "fish_sales": ["buyer_id:integer>buyers.id", "catch_id:integer>catches.id", "weight_kg:numeric!", "total_amount:numeric!", "sale_date:date!"],
  "equipment": ["name!", "quantity:integer", "condition", "cost:numeric"],
  "expenses": E}),
"courier": ("Courier & Delivery", "🛵", "Transport", "Customers, riders, deliveries, fees and incidents.",
  ["courier", "delivery", "dispatch", "rider", "parcel", "packages", "logistics"], {
  "customers": CU,
  "riders": ["name!", "phone", "vehicle"],
  "deliveries": ["customer_id:integer!>customers.id", "rider_id:integer>riders.id", "pickup_address", "dropoff_address", "delivery_date:date!", "fee:numeric!", "status"],
  "packages": ["delivery_id:integer!>deliveries.id", "description!", "weight_kg:numeric", "declared_value:numeric"],
  "payments": ["delivery_id:integer!>deliveries.id", "amount:numeric!", "payment_date:date!"],
  "incidents": ["delivery_id:integer!>deliveries.id", "description!", "cost:numeric"],
  "expenses": E}),
"savings": ("Savings Group (Susu)", "🤝", "Finance", "Members, contributions, loans, repayments and meetings.",
  ["savings", "susu", "susu club", "microfinance", "vsla", "cooperative", "loans group", "credit union"], {
  "savings_groups": ["name!", "meeting_day", "contribution_amount:numeric"],
  "members": ["full_name!", "phone", "group_id:integer>savings_groups.id", "join_date:date!", "address"],
  "contributions": ["member_id:integer!>members.id", "amount:numeric!", "contribution_date:date!"],
  "loans": ["member_id:integer!>members.id", "amount:numeric!", "interest_percent:numeric", "issue_date:date!", "due_date:date", "status"],
  "loan_repayments": ["loan_id:integer!>loans.id", "amount:numeric!", "payment_date:date!"],
  "withdrawals": ["member_id:integer!>members.id", "amount:numeric!", "withdrawal_date:date!"],
  "meetings": ["group_id:integer!>savings_groups.id", "meeting_date:date!", "attendance_count:integer", "notes"]}),
"cyber_cafe": ("Internet Café & Printing", "🖨️", "Services", "Services, computers, sessions, supplies and daily sales.",
  ["cyber cafe", "internet cafe", "printing", "photocopy", "computer center", "business center", "typing"], {
  "services": ["name!", "price:numeric!"],
  "customers": CU,
  "sales": ["customer_id:integer>customers.id", "service_id:integer!>services.id", "quantity:integer!", "total_amount:numeric!", "sale_date:date!"],
  "computers": ["name!", "status", "purchase_date:date"],
  "computer_sessions": ["computer_id:integer!>computers.id", "customer_id:integer>customers.id", "start_time:timestamp!", "minutes:integer", "amount:numeric"],
  "supplies": ["item!", "quantity:integer", "cost:numeric", "purchase_date:date"],
  "expenses": E}),
}

# Display info for the original 5 blueprints
META = {
 "supermarket": ("Supermarket & Shop", "🛒", "Retail", "Products, stock, suppliers, customers, sales and daily profit."),
 "farm": ("Crop Farm", "🌾", "Agriculture", "Crops, workers, expenses, harvests, buyers and sales."),
 "school": ("School", "🎓", "Education", "Students, teachers, classes, fees, results and attendance."),
 "pharmacy": ("Pharmacy", "💊", "Health", "Medicines, expiry dates, suppliers, prescriptions and sales."),
 "restaurant": ("Restaurant & Café", "🍽️", "Hospitality", "Menu, orders, ingredients, staff and expenses."),
}


def col(s):
    s, _, ref = s.partition(">")
    req = s.endswith("!")
    name, _, typ = s.rstrip("!").partition(":")
    return {"name": name, "type": typ or "text", "required": req, "primary_key": False, "references": ref or None}


os.makedirs("blueprints", exist_ok=True)
for slug, (title, icon, cat, desc, kw, tables) in B.items():
    out = {"name": slug, "title": title, "icon": icon, "category": cat, "description": desc, "keywords": kw, "tables": []}
    for tname, cols in tables.items():
        idcol = {"name": "id", "type": "integer", "required": True, "primary_key": True, "references": None}
        out["tables"].append({"name": tname, "columns": [idcol] + [col(c) for c in cols]})
    with open(f"blueprints/{slug}.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)

for slug, (title, icon, cat, desc) in META.items():
    p = f"blueprints/{slug}.json"
    if os.path.exists(p):
        d = json.load(open(p, encoding="utf-8"))
        d.update({"title": title, "icon": icon, "category": cat, "description": desc})
        with open(p, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=1, ensure_ascii=False)

print("blueprints on disk:", len([f for f in os.listdir("blueprints") if f.endswith(".json")]))
