"""Auditable property screening. No estimates can confer verified status."""
from datetime import datetime, timezone, timedelta
from pathlib import Path
import json

DATA = Path(__file__).with_name('real_estate_data.json')
REQUIRED = ('identity', 'year', 'status', 'rental_rules', 'safety', 'rent_comps',
            'tax', 'hoa', 'insurance', 'condition', 'lender')


def load_data():
    return json.loads(DATA.read_text(encoding='utf-8'))


def payment(principal, annual_rate=.075, months=360):
    r = annual_rate / 12
    return principal * r / (1 - (1 + r) ** -months)


def analyze(p, rent=None):
    """All amounts USD. Reserves are cash allocation, not duplicate expense."""
    price = p['price']
    rent = p['rent_scenario'] if rent is None else rent
    mortgage, heloc = price * .75, price * .25
    pi, hi = payment(mortgage), heloc * .09 / 12
    # Vacancy, management, repairs, capital replacements, annual leasing/turnover.
    variable_fraction = .05 + .08 + .05 + .05 + .03
    fixed = p['tax_scenario'] / 12 + p['hoa_scenario'] + p['insurance_scenario'] + p['utilities_scenario'] + p['other_scenario']
    operating = fixed + rent * variable_fraction
    cash_noi = rent - operating
    # NOI convention excludes capital replacement reserve; cash flow includes it.
    noi = cash_noi + rent * .05
    debt = pi + hi
    closing = price * .04  # estimate: title, transfer, lender/DSCR points, HELOC fees
    initial_repairs = p.get('initial_repairs_scenario', 2500)
    minimum_reserve = 6 * (fixed + debt)
    savings = max(30000, closing + initial_repairs + minimum_reserve)
    cf = cash_noi - debt
    balance12 = mortgage * (1 + .075 / 12) ** 12 - pi * (((1 + .075 / 12) ** 12 - 1) / (.075 / 12))
    return dict(price=price, mortgage=mortgage, heloc=heloc, total_debt=mortgage + heloc,
                pi=pi, heloc_interest=hi, debt_service=debt, rent=rent, operating=operating,
                fixed=fixed, cash_noi=cash_noi, noi=noi, cash_flow=cf, closing=closing,
                initial_repairs=initial_repairs, minimum_reserve=minimum_reserve,
                reserves=savings-closing-initial_repairs, savings=savings,
                cap_rate=12*noi/price, cash_on_cash=12*cf/savings,
                dscr= noi / pi, combined_dscr=cash_noi/debt,
                lender_ratio=rent/(pi+p['tax_scenario']/12+p['hoa_scenario']+p['insurance_scenario']),
                break_even=(fixed+debt)/(1-variable_fraction),
                target_rent=(fixed+debt+250)/(1-variable_fraction),
                principal_year1=mortgage-balance12)


def blockers(p, m, now=None):
    now = now or datetime.now(timezone.utc)
    out = []
    for field in REQUIRED:
        ev = p['evidence'].get(field, {})
        if ev.get('status') != 'verified' or not ev.get('url') or not ev.get('observed_at'):
            out.append(f'{field}: {ev.get("status", "missing")}')
        else:
            try:
                observed = datetime.fromisoformat(ev['observed_at'].replace('Z', '+00:00'))
                if not timedelta(0) <= now-observed <= timedelta(days=7):
                    out.append(f'{field}: stale or future evidence')
            except (ValueError, TypeError):
                out.append(f'{field}: invalid evidence timestamp')
    if p.get('property_type') not in ('condo', 'townhome'): out.append('Not condo/townhome')
    if p.get('beds') not in (2, 3): out.append('Not 2–3 bedrooms')
    if (p.get('year') or 0) < 2001: out.append('Construction before 2001 or unknown')
    if p.get('listing_status') != 'active': out.append('Active status not established')
    if p.get('unrestricted_rentals') is not True: out.append('Unrestricted investor rentals not established')
    if p.get('safety_pass') is not True: out.append('Objective area safety review incomplete')
    if m['cash_flow'] < 250: out.append('Cash flow below $250/month')
    if not 30000 <= m['savings'] <= 40000: out.append('Outside $30k–$40k savings budget')
    if m['dscr'] < 1.2: out.append('NOI DSCR below 1.20 screening floor')
    return out


def render():
    import pandas as pd
    import streamlit as st
    data = load_data()
    st.header('Real Estate Investments')
    st.caption('Purdue • Michigan State • Case Western markets | USD | No guaranteed returns')
    st.info('Fixed financing: 25% HELOC at 9% interest-only + 75% mortgage at 7.5%, 30 years. '
            'Total purchase debt is 100% of price; the HELOC also puts existing home equity at risk.')
    st.caption(f"Evidence reviewed {data['reviewed_at']}. Page reloads do not refresh property evidence. "
               'Verified evidence expires after 7 days. Listing observations can change sooner.')
    st.markdown('**Strict criteria:** 2–3 bedrooms; condo/townhome; built 2001+; active; unrestricted investor rentals; '
                'documented safety review; verified rent comps, tax and HOA; ≥$250/month after both loans; '
                '$30k–$40k savings including closing, repairs and reserves. HOA ≤$200 is a preference, as requested. '
                'NOI DSCR ≥1.20 is an explicit screening convention; lender acceptance must also be documented.')
    rows, qualified = [], []
    for p in data['properties']:
        m = analyze(p)
        failures = blockers(p, m)
        if not failures: qualified.append(p)
        rows.append((p, m, failures))
    st.subheader('1 · Strictly Qualified')
    if not qualified:
        st.warning('0 strictly qualified properties. No threshold has been lowered to populate this section.')
    else:
        for p in qualified: st.write(p['address'])
    st.subheader('2 · Lowest Cost / Highest ROI')
    st.write('Research candidates only. Rankings compare stated rent/cost scenarios, not verified achievable returns or the entire market.')
    order = st.radio('Rank candidates', ['Scenario cash-on-cash (highest first)', 'Purchase price (lowest first)'], horizontal=True)
    rows.sort(key=(lambda x: -x[1]['cash_on_cash']) if order.startswith('Scenario') else lambda x: x[1]['price'])
    summary = []
    for p, m, failures in rows:
        summary.append({'Property':p['address'], 'Price $':m['price'], 'Status evidence':p['listing_status'],
                        'Scenario rent $/mo':m['rent'], 'Cash flow $/mo':round(m['cash_flow']),
                        'Scenario CoC %':round(m['cash_on_cash']*100,2), 'NOI cap %':round(m['cap_rate']*100,2),
                        'Savings $':round(m['savings']), 'Break-even rent $':round(m['break_even']),
                        'NOI DSCR':round(m['dscr'],2), 'Qualified':not failures})
    st.dataframe(pd.DataFrame(summary), hide_index=True, width='stretch')
    st.download_button('Download scenario comparison CSV', pd.DataFrame(summary).to_csv(index=False), 'real-estate-scenarios.csv', 'text/csv')
    for p, m, failures in rows:
        with st.expander(f"{p['address']} · ${p['price']:,} · NOT QUALIFIED" if failures else p['address']):
            st.markdown(f"[Sale listing]({p['listing_url']}) · {p['beds']} bedrooms · {p['property_type']} · reported year {p['year']}")
            st.write(p['notes'])
            st.warning('Qualification blockers: ' + '; '.join(failures)) if failures else st.success('All gates passed')
            if p['hoa_scenario'] > 200: st.caption('HOA exceeds the preferred $200/month ceiling.')
            st.write('Rent evidence: ' + p['rent_notes'])
            st.write('Tax/HOA caveats: ' + p['tax_hoa_notes'])
            financing = {'Purchase price':m['price'], 'HELOC draw':m['heloc'], 'Mortgage':m['mortgage'],
                         'Total debt (both loans)':m['total_debt'], 'Savings cash allocated':m['savings'],
                         'Closing/loan costs (4% estimate)':m['closing'], 'Initial repair/furnishing estimate':m['initial_repairs'],
                         'Cash reserves retained':m['reserves'], 'Minimum 6-month carrying reserve':m['minimum_reserve']}
            st.dataframe(pd.DataFrame([{'Upfront financing / cash':'%s' % k, 'USD':round(v,2)} for k,v in financing.items()]), hide_index=True)
            costs = {'Scheduled rent (scenario)':m['rent'], 'Vacancy 5%':m['rent']*.05,
                     'Management 8% of scheduled rent':m['rent']*.08, 'Maintenance 5%':m['rent']*.05,
                     'Capital replacement reserve 5%':m['rent']*.05, 'Leasing/turnover reserve 3%':m['rent']*.03,
                     'Property tax (scenario)':p['tax_scenario']/12, 'HOA (reported; scenario)':p['hoa_scenario'],
                     'Insurance estimate':p['insurance_scenario'], 'Owner utilities estimate':p['utilities_scenario'],
                     'Other / license / assessment contingency':p['other_scenario'], 'Mortgage P&I':m['pi'],
                     'HELOC interest':m['heloc_interest'], 'Net monthly cash flow':m['cash_flow']}
            st.dataframe(pd.DataFrame([{'Monthly income / cost':k,'USD':round(v,2)} for k,v in costs.items()]), hide_index=True)
            st.write(f"NOI cap rate {m['cap_rate']:.2%}; cash-on-cash {m['cash_on_cash']:.2%}; "
                     f"NOI/mortgage DSCR {m['dscr']:.2f}; cash NOI/both loans {m['combined_dscr']:.2f}; "
                     f"gross rent/PITIA {m['lender_ratio']:.2f} (lenders use different definitions). "
                     f"Break-even rent ${m['break_even']:,.0f}; rent for $250 cash flow ${m['target_rent']:,.0f}.")
            st.caption('NOI excludes debt service and capital replacement reserves; cash flow includes both loans and capital reserves. '
                       'CoC = annual cash flow / all savings allocated, including retained reserves. '
                       'Insurance, repairs, vacancy, utilities, fees and future assessments are estimates, not quotes. '
                       'Income tax, depreciation and personal tax benefits excluded. No double counting of initial cash reserves as an expense.')
            sensitivity=[]
            for change in (-.10,-.05,0,.03,.05):
                value=m['price']*(1+change)
                net=value*.94-(m['mortgage']-m['principal_year1'])-m['heloc']
                sensitivity.append({'Year-1 price change':f'{change:.0%}', 'Value $':round(value),
                                    'Net property equity after 6% sale costs $':round(net),
                                    'Cash flow + principal + value change $':round(12*m['cash_flow']+m['principal_year1']+m['price']*change)})
            st.dataframe(pd.DataFrame(sensitivity), hide_index=True)
            st.caption('ROE on initial net property equity is undefined: mortgage + HELOC equal purchase price. '
                       'HELOC is legally secured elsewhere but is included economically. Appreciation is a scenario, not income or a forecast. '
                       'Net sale equity excludes retained savings reserves; negative means more cash required. '
                       'The final column is pre-tax economic change before selling costs, not realized profit or CoC.')
            stressed=analyze(p,m['rent']*.9)
            st.write(f"10% rent reduction: ${stressed['cash_flow']:,.0f}/month cash flow. "
                     f"HELOC +2 percentage points: ${m['cash_flow']-m['heloc']*.02/12:,.0f}/month. "
                     'A $5,000 special assessment reduces annual cash flow by $5,000.')
            st.markdown('**Evidence register — a public listing report is not independent verification**')
            for field, ev in p['evidence'].items():
                link=f"[source]({ev['url']})" if ev.get('url') else 'No supporting document obtained'
                st.markdown(f"- **{field} — {ev['status']}**: {ev['detail']} · {link} · {ev.get('observed_at', 'not checked')}")
    with st.expander('Update evidence / methodology / research limitations'):
        st.write(data['update_instructions'])
        st.download_button('Download full evidence JSON', json.dumps(data,indent=2), 'real-estate-evidence.json','application/json')
        st.write('No broker/HOA replies or lender approval were obtained. Public-record portals were located but parcel bills and governing documents '
                 'were not retrieved. There is no connected phone/HOA document feed. Missing evidence blocks qualification. '
                 'Safety remains unknown until dated official incident data, geography and physical risk review are documented; '
                 'schools, demographics and marketing descriptions are not safety proxies.')
