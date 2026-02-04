import random
from datetime import date, timedelta
from fpdf import FPDF

class PDF(FPDF):
    def header(self):
        # Header is now customized per invoice in the loop
        pass

    def footer(self):
        self.set_y(-15)
        self.set_font('Arial', 'I', 8)
        self.cell(0, 10, 'Page ' + str(self.page_no()) + '/{nb}', 0, 0, 'C')

def generate_pdf(filename, company, client, inv_num, inv_date, items):
    pdf = PDF()
    pdf.alias_nb_pages()
    pdf.add_page()
    
    # Custom Header
    pdf.set_font('Arial', 'B', 20)
    pdf.cell(0, 10, company['name'], 0, 1, 'L')
    pdf.set_font('Arial', '', 10)
    pdf.cell(0, 5, company['address'], 0, 1, 'L')
    pdf.cell(0, 5, company['city'], 0, 1, 'L')
    pdf.cell(0, 5, f"SIRET: {company['siret']}", 0, 1, 'L')
    pdf.cell(0, 5, f"Email: {company['email']}", 0, 1, 'L')
    pdf.ln(20)

    # Client Info
    pdf.set_font('Arial', 'B', 12)
    pdf.cell(0, 10, 'FACTURE A:', 0, 1, 'L')
    pdf.set_font('Arial', '', 11)
    pdf.cell(0, 5, client['name'], 0, 1, 'L')
    pdf.cell(0, 5, client['address'], 0, 1, 'L')
    pdf.cell(0, 5, client['city'], 0, 1, 'L')
    
    # Invoice Details
    pdf.set_xy(120, 55)
    pdf.set_font('Arial', 'B', 14)
    pdf.cell(70, 10, 'FACTURE', 0, 1, 'R')
    
    pdf.set_xy(120, 65)
    pdf.set_font('Arial', '', 11)
    pdf.cell(35, 7, 'Numero:', 0, 0, 'R')
    pdf.cell(35, 7, inv_num, 0, 1, 'R')
    
    pdf.set_xy(120, 72)
    pdf.cell(35, 7, 'Date:', 0, 0, 'R')
    pdf.cell(35, 7, inv_date.strftime("%d/%m/%Y"), 0, 1, 'R')
    
    due_date = inv_date + timedelta(days=30)
    pdf.set_xy(120, 79)
    pdf.cell(35, 7, 'Echeance:', 0, 0, 'R')
    pdf.cell(35, 7, due_date.strftime("%d/%m/%Y"), 0, 1, 'R')

    pdf.ln(30)

    # Table Header
    pdf.set_fill_color(240, 240, 240)
    pdf.set_font('Arial', 'B', 11)
    pdf.cell(90, 10, 'Description', 1, 0, 'L', 1)
    pdf.cell(25, 10, 'Qte', 1, 0, 'C', 1)
    pdf.cell(35, 10, 'Prix Unit. HT', 1, 0, 'R', 1)
    pdf.cell(40, 10, 'Total HT', 1, 1, 'R', 1)

    # Items
    pdf.set_font('Arial', '', 11)
    total_ht = 0
    for desc, qty, price in items:
        line_total = qty * price
        total_ht += line_total
        pdf.cell(90, 10, desc, 1)
        pdf.cell(25, 10, str(qty), 1, 0, 'C')
        pdf.cell(35, 10, f"{price:.2f} EUR", 1, 0, 'R')
        pdf.cell(40, 10, f"{line_total:.2f} EUR", 1, 1, 'R')

    # Totals
    tva = total_ht * 0.20
    total_ttc = total_ht + tva

    pdf.ln(5)
    
    def total_line(label, value, bold=False):
        pdf.set_x(130)
        pdf.set_font('Arial', 'B' if bold else '', 11)
        pdf.cell(30, 8, label, 0, 0, 'R')
        pdf.cell(30, 8, f"{value:,.2f} EUR".replace(",", " "), 0, 1, 'R')

    total_line("Sous-Total HT:", total_ht)
    total_line("TVA (20%):", tva)
    pdf.ln(2)
    pdf.set_text_color(0, 50, 150)
    total_line("NET A PAYER:", total_ttc, bold=True)
    pdf.set_text_color(0)

    # Payment Info
    pdf.ln(20)
    pdf.set_font('Arial', 'B', 10)
    pdf.cell(0, 5, 'Informations de paiement:', 0, 1)
    pdf.set_font('Arial', '', 10)
    pdf.cell(0, 5, f"Banque: {company['bank']}", 0, 1)
    pdf.cell(0, 5, f"IBAN: {company['iban']}", 0, 1)
    
    pdf.output(filename, 'F')
    print(f"PDF genere: {filename}")

def main():
    # Invoice 1: Cleaning Service
    company1 = {
        'name': 'CLEAN PRO SERVICE',
        'address': '10 Rue de la Paix',
        'city': '75002 Paris',
        'siret': '987 654 321 00045',
        'email': 'billing@cleanpro.fr',
        'bank': 'BNP Paribas',
        'iban': 'FR76 3000 4028 3760 1234 5678 901'
    }
    client1 = {
        'name': 'StartUp Hub',
        'address': '55 Boulevard Haussmann',
        'city': '75009 Paris'
    }
    items1 = [
        ("Nettoyage bureaux (Janvier 2026)", 1, 1200.00),
        ("Fourniture consommables sanitaires", 1, 150.50),
        ("Desinfection Covid-19 hebdo", 4, 250.00)
    ]
    generate_pdf(
        "data/raw_pdfs/facture_cleanpro.pdf", 
        company1, client1, "FAC-2026-001", date(2026, 1, 31), items1
    )

    # Invoice 2: Cloud Hosting
    company2 = {
        'name': 'CLOUD FAST SAS',
        'address': 'Zone Industrielle Nord',
        'city': '59000 Lille',
        'siret': '111 222 333 00099',
        'email': 'support@cloudfast.eu',
        'bank': 'Societe Generale',
        'iban': 'FR76 3000 3022 5432 1234 5678 901'
    }
    client2 = {
        'name': 'Martin Dupont',
        'address': '45 Rue des Lilas',
        'city': '69002 Lyon'
    }
    items2 = [
        ("Hebergement Serveur Dedie (Mensuel)", 1, 89.99),
        ("Option Backup Quotidien", 1, 15.00),
        ("Nom de domaine .com (Renouvellement)", 1, 12.00)
    ]
    generate_pdf(
        "data/raw_pdfs/facture_cloudfast.pdf",
        company2, client2, "INV-998877", date(2026, 2, 1), items2
    )

if __name__ == '__main__':
    main()
