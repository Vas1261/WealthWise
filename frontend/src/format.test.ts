import { describe, expect, it } from 'vitest'
import {
  MISSING,
  currentMonth,
  formatDate,
  formatMoney,
  formatMonth,
  formatPercent,
  formatPrice,
  formatQuantity,
  formatRate,
  isNegative,
  label,
} from './format'

describe('money is formatted exactly from decimal strings', () => {
  it('keeps all 18 whole digits and both decimals (a float would round)', () => {
    expect(formatMoney('999999999999999999.99', 'INR', 'en-US')).toBe('₹999,999,999,999,999,999.99')
    expect(formatMoney('123456789012345678.91', 'EUR', 'en-US')).toBe('€123,456,789,012,345,678.91')
    expect(formatMoney('876543210987654321.09', 'INR', 'en-IN')).toBe('₹8,76,54,32,10,98,76,54,321.09')
  })

  it('keeps signs and small values', () => {
    expect(formatMoney('-50.25', 'INR', 'en-US')).toBe('-₹50.25')
    expect(formatMoney('0.01', 'USD', 'en-US')).toBe('$0.01')
    expect(formatMoney('0.00', 'INR', 'en-US')).toBe('₹0.00')
  })

  it('follows the locale it is given (U7: the browser locale by default)', () => {
    expect(formatMoney('1499.50', 'EUR', 'de-DE')).toBe('1.499,50 €')
  })

  it('shows two decimals for every currency, as stored', () => {
    expect(formatMoney('1499.50', 'JPY', 'en-US')).toBe('¥1,499.50')
  })

  it('shows a dash for a missing value, never zero', () => {
    expect(formatMoney(null, 'INR')).toBe(MISSING)
    expect(formatMoney(undefined, 'INR')).toBe(MISSING)
    expect(formatPercent(null)).toBe(MISSING)
    expect(formatPrice(null, 'INR')).toBe(MISSING)
    expect(formatRate(null)).toBe(MISSING)
  })

  it('falls back to number + code for a code Intl does not know', () => {
    expect(formatMoney('12.50', 'ZZ', 'en-US')).toBe('12.50 ZZ')
  })
})

describe('other values', () => {
  it('formats percentages, prices, quantities and rates without changing their digits', () => {
    expect(formatPercent('10.00', 'en-US')).toBe('10.00 %')
    expect(formatPercent('-99.99', 'en-US')).toBe('-99.99 %')
    expect(formatPrice('1020.5000', 'INR', 'en-US')).toBe('₹1,020.5000')
    expect(formatQuantity('17.583000', 'en-US')).toBe('17.583')
    expect(formatQuantity('1000000.000001', 'en-US')).toBe('1,000,000.000001')
    expect(formatRate('0.009240', 'en-US')).toBe('0.009240')
  })

  it('shows calendar dates without a time-zone shift', () => {
    expect(formatDate('2026-01-01', 'en-US')).toBe('Jan 1, 2026')
    expect(formatDate('2026-12-31', 'en-US')).toBe('Dec 31, 2026')
    expect(formatDate(null)).toBe(MISSING)
    expect(formatMonth('2026-10', 'en-US')).toBe('October 2026')
  })

  it('takes the current month from the local date', () => {
    expect(currentMonth(new Date(2026, 0, 31, 23, 59))).toBe('2026-01')
    expect(currentMonth(new Date(2026, 11, 1, 0, 0))).toBe('2026-12')
  })

  it('reads a sign only for styling', () => {
    expect(isNegative('-0.01')).toBe(true)
    expect(isNegative('0.00')).toBe(false)
    expect(isNegative(null)).toBe(false)
  })

  it('labels backend enum values', () => {
    expect([label('mutual_fund'), label('zerodha'), label('groww'), label('equity'), label(null)]).toEqual([
      'Mutual funds', 'Zerodha', 'Groww', 'Equity', MISSING,
    ])
  })
})
