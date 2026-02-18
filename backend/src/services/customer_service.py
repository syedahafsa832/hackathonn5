from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from typing import Optional
import uuid

from ..models.customer import Customer
from ..models.customer_identifier import CustomerIdentifier


async def get_or_create_customer(
    db: AsyncSession,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    name: str = "Anonymous",
    company: Optional[str] = None
) -> Customer:
    """
    Get existing customer by email or phone, or create new one
    """
    customer = None
    
    # 1. Try to find by email
    if email:
        result = await db.execute(select(Customer).where(Customer.email == email))
        customer = result.scalar_one_or_none()
    
    # 2. Try to find by phone if not found by email
    if not customer and phone:
        result = await db.execute(select(Customer).where(Customer.phone == phone))
        customer = result.scalar_one_or_none()

    if customer:
        # Update customer info if it has changed
        if name and name != "Anonymous":
            customer.name = name
        if company:
            customer.company = company
        if phone and not customer.phone:
            customer.phone = phone
        if email and not customer.email:
            customer.email = email
        await db.flush()
        return customer

    # Create new customer
    customer = Customer(
        email=email,
        phone=phone,
        name=name,
        company=company
    )

    db.add(customer)
    await db.flush()

    # Create primary identifiers
    if email:
        db.add(CustomerIdentifier(customer_id=customer.id, identifier_type="email", identifier_value=email, is_primary=True))
    if phone:
        db.add(CustomerIdentifier(customer_id=customer.id, identifier_type="phone", identifier_value=phone, is_primary=(not bool(email))))
    
    await db.flush()
    return customer


async def get_customer_by_id(
    db: AsyncSession,
    customer_id: uuid.UUID
) -> Optional[Customer]:
    """
    Retrieve a customer by their ID
    """
    result = await db.execute(
        select(Customer).where(Customer.id == customer_id)
    )
    return result.scalar_one_or_none()


async def get_customer_by_identifier(
    db: AsyncSession,
    identifier_value: str,
    identifier_type: str = "email"
) -> Optional[Customer]:
    """
    Retrieve a customer by their identifier (email, phone, etc.)
    """
    # First, find the customer identifier
    identifier_result = await db.execute(
        select(CustomerIdentifier).where(
            and_(
                CustomerIdentifier.identifier_value == identifier_value,
                CustomerIdentifier.identifier_type == identifier_type
            )
        )
    )
    customer_identifier = identifier_result.scalar_one_or_none()

    if not customer_identifier:
        return None

    # Then get the customer
    customer_result = await db.execute(
        select(Customer).where(Customer.id == customer_identifier.customer_id)
    )
    return customer_result.scalar_one_or_none()


async def create_customer_identifier(
    db: AsyncSession,
    customer_id: uuid.UUID,
    identifier_type: str,
    identifier_value: str,
    is_primary: bool = False
) -> CustomerIdentifier:
    """
    Create a new identifier for an existing customer
    """
    # Check if identifier already exists
    existing_result = await db.execute(
        select(CustomerIdentifier).where(
            and_(
                CustomerIdentifier.identifier_type == identifier_type,
                CustomerIdentifier.identifier_value == identifier_value
            )
        )
    )
    existing_identifier = existing_result.scalar_one_or_none()

    if existing_identifier:
        raise ValueError(f"Identifier {identifier_type}:{identifier_value} already exists for another customer")

    customer_identifier = CustomerIdentifier(
        customer_id=customer_id,
        identifier_type=identifier_type,
        identifier_value=identifier_value,
        is_primary=is_primary
    )

    db.add(customer_identifier)
    await db.flush()

    return customer_identifier
