"""Transparent 'loss avoided' estimate for the pitch. All inputs are assumptions the user can edit."""


def estimate_loss_avoided(tonnes: float, price_per_quintal: float, damage_fraction: float, prevented_fraction: float) -> dict:
    """damage_fraction: share of the stock value lost if a failure goes unnoticed (0-1).
    prevented_fraction: share of that loss that an early alert prevents (0-1)."""
    stock_value = tonnes * 10 * price_per_quintal          # 1 tonne = 10 quintal
    loss_if_unnoticed = stock_value * damage_fraction
    avoided = loss_if_unnoticed * prevented_fraction
    return {"stock_value": stock_value, "loss_if_unnoticed": loss_if_unnoticed, "loss_avoided": avoided}


def payback(loss_avoided_per_event: float, events_per_year: float, kit_price: float = 4000.0, monthly_fee: float = 1200.0) -> dict:
    annual_cost = kit_price + 12 * monthly_fee
    annual_benefit = loss_avoided_per_event * events_per_year
    return {"annual_cost": annual_cost, "annual_benefit": annual_benefit,
            "benefit_cost_ratio": annual_benefit / annual_cost if annual_cost else None}
