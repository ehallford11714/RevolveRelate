"""Tableau-style Superstore: normalized Customers, Products, Orders, OrderLine."""

from __future__ import annotations

import sqlite3
from pathlib import Path

CUSTOMERS = [
    (1, "CG-12520", "Claire Gute", "Consumer", "United States", "Henderson", "Kentucky", "South", "claire.gute@example.com"),
    (2, "DV-13045", "Darrin Van Huff", "Corporate", "United States", "Los Angeles", "California", "West", "darrin.huff@example.com"),
    (3, "SO-20335", "Sean O'Donnell", "Consumer", "United States", "Fort Lauderdale", "Florida", "South", "sean.odonnell@example.com"),
    (4, "BH-11710", "Brosina Hoffman", "Consumer", "United States", "Los Angeles", "California", "West", "brosina.hoffman@example.com"),
    (5, "AA-10480", "Andrew Allen", "Consumer", "United States", "Concord", "North Carolina", "South", "andrew.allen@example.com"),
    (6, "IM-15070", "Irene Maddox", "Consumer", "United States", "Seattle", "Washington", "West", "irene.maddox@example.com"),
    (7, "HP-14815", "Harold Pawlan", "Home Office", "United States", "Fort Worth", "Texas", "Central", "harold.pawlan@example.com"),
    (8, "PK-19075", "Pete Kriz", "Consumer", "United States", "Madison", "Wisconsin", "Central", "pete.kriz@example.com"),
    (9, "AG-10270", "Alejandro Grove", "Consumer", "United States", "West Jordan", "Utah", "West", "alejandro.grove@example.com"),
    (10, "ZD-21925", "Zuschuss Donatelli", "Consumer", "United States", "San Francisco", "California", "West", "zuschuss.donatelli@example.com"),
]

PRODUCTS = [
    (1, "FUR-BO-10001798", "Furniture", "Bookcases", "Bush Somerset Collection Bookcase"),
    (2, "FUR-CH-10000454", "Furniture", "Chairs", "Hon Deluxe Fabric Upholstered Stacking Chair"),
    (3, "OFF-LA-10000240", "Office Supplies", "Labels", "Self-Adhesive Address Labels for Typewriters"),
    (4, "TEC-PH-10002275", "Technology", "Phones", "Mitel 5320 IP Phone VoIP phone"),
    (5, "OFF-ST-10000760", "Office Supplies", "Storage", "Eldon Fold 'N Roll Cart System"),
    (6, "FUR-TA-10000577", "Furniture", "Tables", "Bretford CR4500 Series Slim Rectangular Table"),
    (7, "TEC-AC-10003033", "Technology", "Accessories", "Logitech Illuminated Keyboard"),
    (8, "OFF-BI-10003910", "Office Supplies", "Binders", "DXL Angle-View Binders with Locking Rings"),
]

ORDERS = [
    (1, "CA-2016-152156", 1, "2016-11-08", "2016-11-11", "Second Class"),
    (2, "CA-2016-138688", 2, "2016-06-12", "2016-06-16", "Second Class"),
    (3, "US-2015-108966", 3, "2015-10-11", "2015-10-18", "Standard Class"),
    (4, "CA-2014-115812", 4, "2014-06-09", "2014-06-14", "Standard Class"),
    (5, "CA-2017-114412", 5, "2017-04-15", "2017-04-20", "Standard Class"),
    (6, "CA-2016-161389", 6, "2016-12-05", "2016-12-10", "Standard Class"),
    (7, "US-2015-118983", 7, "2015-11-22", "2015-11-26", "Standard Class"),
    (8, "CA-2014-105893", 8, "2014-11-11", "2014-11-18", "Standard Class"),
    (9, "CA-2014-167164", 9, "2014-05-13", "2014-05-15", "Second Class"),
    (10, "CA-2014-143336", 10, "2014-08-27", "2014-09-01", "Second Class"),
    (11, "CA-2016-137330", 2, "2016-12-09", "2016-12-13", "Standard Class"),
    (12, "US-2017-156909", 4, "2017-07-16", "2017-07-18", "Second Class"),
]

# LineId, OrderId, ProductId, Sales, Quantity, Discount, Profit
LINES = [
    (1, 1, 1, 261.96, 2, 0.00, 41.91),
    (2, 1, 2, 731.94, 3, 0.00, 219.58),
    (3, 2, 3, 14.62, 2, 0.00, 6.87),
    (4, 3, 6, 957.58, 5, 0.45, -383.03),
    (5, 3, 5, 22.37, 2, 0.20, 2.52),
    (6, 4, 1, 48.86, 7, 0.00, 14.17),
    (7, 4, 7, 907.15, 4, 0.20, 90.72),
    (8, 4, 4, 68.81, 6, 0.20, 5.48),
    (9, 5, 8, 2.54, 3, 0.20, 0.86),
    (10, 6, 7, 665.88, 6, 0.00, 13.32),
    (11, 7, 8, 15.55, 3, 0.80, -13.22),
    (12, 8, 5, 212.06, 4, 0.20, 15.90),
    (13, 9, 5, 19.46, 3, 0.00, 5.06),
    (14, 10, 4, 213.48, 3, 0.20, 16.01),
    (15, 11, 2, 2573.82, 9, 0.00, 746.41),
    (16, 12, 2, 71.37, 2, 0.30, -1.02),
]


def write_superstore(path: str | Path) -> Path:
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    conn = sqlite3.connect(str(dest))
    conn.executescript(
        """
        CREATE TABLE Customer (
            CustomerId INTEGER PRIMARY KEY,
            CustomerCode TEXT NOT NULL UNIQUE,
            CustomerName TEXT NOT NULL,
            Segment TEXT,
            Country TEXT,
            City TEXT,
            State TEXT,
            Region TEXT,
            Email TEXT
        );
        CREATE TABLE Product (
            ProductId INTEGER PRIMARY KEY,
            ProductCode TEXT NOT NULL UNIQUE,
            Category TEXT,
            SubCategory TEXT,
            ProductName TEXT
        );
        CREATE TABLE Orders (
            OrderId INTEGER PRIMARY KEY,
            OrderCode TEXT NOT NULL UNIQUE,
            CustomerId INTEGER NOT NULL,
            OrderDate TEXT,
            ShipDate TEXT,
            ShipMode TEXT,
            FOREIGN KEY (CustomerId) REFERENCES Customer(CustomerId)
        );
        CREATE TABLE OrderLine (
            LineId INTEGER PRIMARY KEY,
            OrderId INTEGER NOT NULL,
            ProductId INTEGER NOT NULL,
            Sales REAL,
            Quantity INTEGER,
            Discount REAL,
            Profit REAL,
            FOREIGN KEY (OrderId) REFERENCES Orders(OrderId),
            FOREIGN KEY (ProductId) REFERENCES Product(ProductId)
        );
        """
    )
    conn.executemany("INSERT INTO Customer VALUES (?,?,?,?,?,?,?,?,?)", CUSTOMERS)
    conn.executemany("INSERT INTO Product VALUES (?,?,?,?,?)", PRODUCTS)
    conn.executemany("INSERT INTO Orders VALUES (?,?,?,?,?,?)", ORDERS)
    conn.executemany("INSERT INTO OrderLine VALUES (?,?,?,?,?,?,?)", LINES)
    conn.commit()
    conn.close()
    return dest


def example_questions() -> list[str]:
    return [
        "customers in West",
        "orders in California",
        "orderlines over 500",
        "products in Technology",
    ]
