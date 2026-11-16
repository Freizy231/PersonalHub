"""Fixed-point quantities and bounded, ordinary diet input."""
from datetime import date
from decimal import Decimal
import re
import unicodedata
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Unit = Literal['g', 'kg', 'ml', 'l', 'piece', 'portion']
FACTORS = {'g': ('g', 1), 'kg': ('g', 1000), 'ml': ('ml', 1), 'l': ('ml', 1000), 'piece': ('piece', 1), 'portion': ('portion', 1)}
MAX_AMOUNT = 1_000_000_000  # milli-units in the submitted unit


def milli(value, limit=MAX_AMOUNT):
    if not isinstance(value, str) or not re.fullmatch(r'(?:0|[1-9][0-9]{0,9})(?:\.[0-9]{1,3})?', value):
        raise ValueError('decimal string, up to three fraction digits')
    result = int(Decimal(value) * 1000)
    if result > limit:
        raise ValueError('quantity too large')
    return result


def amount(value):
    return format(Decimal(value) / 1000, 'f')


def normalized(value):
    return ' '.join(unicodedata.normalize('NFKC', value).split())


class Base(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class Version(Base):
    expected_version: int = Field(ge=1, strict=True)


class IngredientWrite(Base):
    name: str = Field(min_length=1, max_length=80)
    expected_version: int = Field(default=0, ge=0, strict=True)

    @field_validator('name')
    @classmethod
    def valid_name(cls, value):
        value = normalized(value)
        if not value or len(value)>80 or any(unicodedata.category(c).startswith('C') for c in value):
            raise ValueError('invalid ingredient')
        return value


class Quantity(Base):
    quantity: str
    unit: Unit

    @field_validator('quantity')
    @classmethod
    def quantity_valid(cls, value):
        if milli(value)<=0:
            raise ValueError('positive quantity required')
        return value


class RecipeItem(Quantity):
    ingredient_id: UUID


class RecipeWrite(Base):
    title: str = Field(min_length=1, max_length=200)
    instructions: str = Field(default='', max_length=20000)
    base_servings: int = Field(default=1, ge=1, le=100, strict=True)
    favorite: bool = Field(default=False, strict=True)
    ingredients: list[RecipeItem] = Field(default_factory=list, max_length=100)
    expected_version: int = Field(default=0, ge=0, strict=True)

    @model_validator(mode='after')
    def no_duplicate(self):
        pairs=[(r.ingredient_id, FACTORS[r.unit][0]) for r in self.ingredients]
        if len(set(pairs))!=len(pairs):
            raise ValueError('duplicate compatible ingredient lines')
        return self


class DishWrite(Base):
    """One dish in a meal: either a saved recipe or a free-text name."""
    recipe_id: UUID | None = None
    label: str = Field(default='', max_length=200)

    @model_validator(mode='after')
    def exactly_one(self):
        has_recipe = self.recipe_id is not None
        text = ' '.join(self.label.split())
        if has_recipe and text:
            raise ValueError('dish cannot name both a recipe and a free-text label')
        if not has_recipe and not text:
            raise ValueError('dish requires a recipe or a name')
        self.label = text
        return self


class MealWrite(Base):
    local_date: date
    slot: Literal['breakfast', 'lunch', 'dinner']
    # Meals no longer require a recipe: dishes carry the actual food.
    recipe_id: UUID | None = None
    dishes: list[DishWrite] = Field(default_factory=list, max_length=30)
    servings: int = Field(default=1, ge=1, le=100, strict=True)
    note: str = Field(default='', max_length=1000)
    expected_version: int = Field(default=0, ge=0, strict=True)

    @field_validator('local_date')
    @classmethod
    def bounded_date(cls, value):
        if value>date(9999,12,24):
            raise ValueError('date too late for a complete week')
        return value

    @model_validator(mode='after')
    def has_food(self):
        # A meal with neither dishes nor a legacy recipe has nothing to show.
        if not self.dishes and self.recipe_id is None:
            raise ValueError('meal requires at least one dish')
        if len(self.dishes) > 30:
            raise ValueError('too many dishes')
        return self


class ShoppingWrite(Base):
    ingredient_id: UUID
    quantity: str
    unit: Unit
    expected_version: int = Field(ge=0, strict=True)
    note: str = Field(default='', max_length=1000)
    purchased: bool = Field(default=False, strict=True)

    @field_validator('quantity')
    @classmethod
    def positive(cls, v):
        if milli(v, 1_000_000_000_000)<=0:
            raise ValueError('positive quantity required')
        return v


class ShoppingState(Base):
    expected_version: int = Field(ge=1, strict=True)
    removed: bool = Field(strict=True)


class SuggestionSelection(Base):
    ingredient_id: UUID
    unit: Literal['g', 'ml', 'piece', 'portion']


class ConfirmSuggestions(Base):
    expected_version: int = Field(ge=0, strict=True)
    digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    selected: list[SuggestionSelection] = Field(min_length=1, max_length=500)

    @model_validator(mode='after')
    def unique(self):
        if len({(x.ingredient_id,x.unit) for x in self.selected})!=len(self.selected):
            raise ValueError('duplicate selection')
        return self


class StockWrite(Base):
    ingredient_id: UUID
    unit: Literal['g', 'ml', 'piece', 'portion']
    quantity: str
    expected_version: int = Field(ge=0, strict=True)

    @field_validator('quantity')
    @classmethod
    def valid(cls, v):
        milli(v)
        return v


def monday(day):
    if day.weekday()!=0:
        raise ValueError('week_start must be Monday')
    return day.isoformat()
