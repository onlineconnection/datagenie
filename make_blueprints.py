import json, os

# column format: name:type  (! = required, >table.col = foreign key). Default type is text.
# Every table automatically gets an integer "id" primary key.
SPECS = {
 "supermarket": (["supermarket","shop","store","grocery","retail","minimarket"], {
  "categories": ["name!"],
  "suppliers": ["name!", "phone", "email"],
  "products": ["name!", "category_id:integer>categories.id", "supplier_id:integer>suppliers.id", "sku", "cost_price:numeric!", "selling_price:numeric!", "stock_quantity:integer!", "reorder_level:integer"],
  "customers": ["name!", "phone"],
  "sales": ["customer_id:integer>customers.id", "sale_date:date!", "total_amount:numeric!"],
  "sale_items": ["sale_id:integer!>sales.id", "product_id:integer!>products.id", "quantity:integer!", "unit_price:numeric!"],
  "expenses": ["description!", "amount:numeric!", "expense_date:date!"]}),
 "farm": (["farm","farmer","crops","cassava","rice","agriculture","livestock","harvest"], {
  "crops": ["name!", "field_location", "planting_date:date", "expected_harvest_date:date", "status"],
  "workers": ["name!", "phone", "daily_wage:numeric"],
  "expenses": ["crop_id:integer>crops.id", "category!", "description", "amount:numeric!", "expense_date:date!"],
  "harvests": ["crop_id:integer!>crops.id", "harvest_date:date!", "quantity:numeric!", "unit"],
  "buyers": ["name!", "phone"],
  "sales": ["crop_id:integer>crops.id", "buyer_id:integer>buyers.id", "sale_date:date!", "quantity:numeric!", "unit_price:numeric!", "total_amount:numeric!"],
  "livestock": ["animal_type!", "quantity:integer!", "notes"]}),
 "school": (["school","students","teachers","classes","fees","results","education","college"], {
  "classes": ["name!", "level"],
  "teachers": ["name!", "phone", "subject"],
  "students": ["full_name!", "gender", "birth_date:date", "class_id:integer!>classes.id", "guardian_name", "guardian_phone"],
  "subjects": ["name!", "teacher_id:integer>teachers.id"],
  "fees": ["student_id:integer!>students.id", "amount:numeric!", "payment_date:date!", "term"],
  "results": ["student_id:integer!>students.id", "subject_id:integer!>subjects.id", "term!", "score:numeric!"],
  "attendance": ["student_id:integer!>students.id", "attendance_date:date!", "present:boolean!"]}),
 "pharmacy": (["pharmacy","medicine","drugs","clinic","chemist","drug store"], {
  "suppliers": ["name!", "phone"],
  "medicines": ["name!", "generic_name", "category", "supplier_id:integer>suppliers.id", "cost_price:numeric!", "selling_price:numeric!", "stock_quantity:integer!", "expiry_date:date", "reorder_level:integer"],
  "customers": ["name!", "phone"],
  "sales": ["customer_id:integer>customers.id", "sale_date:date!", "total_amount:numeric!"],
  "sale_items": ["sale_id:integer!>sales.id", "medicine_id:integer!>medicines.id", "quantity:integer!", "unit_price:numeric!"],
  "prescriptions": ["customer_id:integer>customers.id", "doctor_name", "issue_date:date", "notes"],
  "expenses": ["description!", "amount:numeric!", "expense_date:date!"]}),
 "restaurant": (["restaurant","cafe","food","bar","kitchen","catering","bakery"], {
  "menu_items": ["name!", "category", "price:numeric!", "is_available:boolean"],
  "ingredients": ["name!", "unit", "stock_quantity:numeric!", "reorder_level:numeric"],
  "suppliers": ["name!", "phone"],
  "orders": ["table_number:integer", "order_date:timestamp!", "status!", "total_amount:numeric"],
  "order_items": ["order_id:integer!>orders.id", "menu_item_id:integer!>menu_items.id", "quantity:integer!", "unit_price:numeric!"],
  "staff": ["name!", "role", "phone", "salary:numeric"],
  "expenses": ["description!", "amount:numeric!", "expense_date:date!"]}),
}

def col(s):
    s, _, ref = s.partition(">")
    req = s.endswith("!")
    name, _, typ = s.rstrip("!").partition(":")
    return {"name": name, "type": typ or "text", "required": req,
            "primary_key": False, "references": ref or None}

os.makedirs("blueprints", exist_ok=True)
for biz, (keywords, tables) in SPECS.items():
    out = {"name": biz, "keywords": keywords, "tables": []}
    for tname, cols in tables.items():
        idcol = {"name": "id", "type": "integer", "required": True, "primary_key": True, "references": None}
        out["tables"].append({"name": tname, "columns": [idcol] + [col(c) for c in cols]})
    with open(f"blueprints/{biz}.json", "w") as f:
        json.dump(out, f, indent=1)
    print(biz, "->", len(out["tables"]), "tables")
