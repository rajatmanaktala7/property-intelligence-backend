INSERT INTO pi_properties
(property_id, property_name, property_type, city, location, available_area_sqft, minimum_area_sqft, maximum_area_sqft, floor, rent_or_sale, possession, nearby_brands, suitable_category, parking, source)
VALUES
('PROP-DEL-0001','Sample High Street Shop','Retail','Delhi','Connaught Place',2500,1800,3000,'Ground','Rent','Immediate','Nike, Adidas, H&M','Fashion, Sports, Lifestyle','Available','Manual Demo'),
('PROP-GGN-0002','Sample Commercial Unit','Retail','Gurugram','Golf Course Road',4200,3000,5000,'Ground + First','Rent','30 Days','Starbucks, Zara','F&B, Fashion, Premium Retail','Available','Manual Demo')
ON CONFLICT (property_id) DO NOTHING;

INSERT INTO pi_requirements
(requirement_id, client_name, company_name, requirement_type, property_type, city, preferred_locations, minimum_area_sqft, maximum_area_sqft, rent_or_sale, nearby_brands, suitable_category, status, source)
VALUES
('REQ-DEL-0001','Demo Client','Demo Retailer','Store Opening','Retail','Delhi','Connaught Place, South Delhi',1800,3000,'Rent','Nike, Adidas','Fashion, Sports','New','Manual Demo')
ON CONFLICT (requirement_id) DO NOTHING;
