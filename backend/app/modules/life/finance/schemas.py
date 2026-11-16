"""Money is decimal text at the API and integer minor units in SQLite."""
from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID
import re
from pydantic import BaseModel,ConfigDict,Field,field_validator

class Base(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)

class Version(Base):
    expected_version:int=Field(ge=1,strict=True)

class CategoryWrite(Base):
    name:str=Field(min_length=1,max_length=80)
    direction:Literal['income','expense']
    expected_version:int=Field(default=0,ge=0,strict=True)

Currency=Literal['CNY','USD','EUR','JPY']
SCALES={'CNY':2,'USD':2,'EUR':2,'JPY':0}

def minor(value,currency,zero=False):
    if not isinstance(value,str) or not re.fullmatch(r'(?:0|[1-9][0-9]{0,12})(?:\.[0-9]{1,2})?',value):raise ValueError('decimal text required')
    if '.' in value and len(value.split('.')[1])>SCALES[currency]:raise ValueError('currency precision')
    n=int(Decimal(value)*(10**SCALES[currency]))
    if n< (0 if zero else 1) or n>10**12:raise ValueError('amount out of range')
    return n

def money(n,currency):return format(Decimal(n)/(10**SCALES[currency]),f'.{SCALES[currency]}f')

def month_valid(v):
    if not re.fullmatch(r'[0-9]{4}-(?:0[1-9]|1[0-2])',v) or v[:4]=='0000':
        from ....core.database import HubError
        raise HubError('MONTH_INVALID',422)
    return v

def month_bounds(v):
    """[first day, first day of next month) as ISO dates, so queries can use the date index.
    `substr(local_date,1,7)=?` is equivalent but forces a full table scan."""
    month_valid(v)
    year,number=int(v[:4]),int(v[5:])
    following=f'{year+1:04d}-01-01' if number==12 else f'{year:04d}-{number+1:02d}-01'
    return v+'-01',following

class EntryWrite(Base):
    local_date:date
    category_id:UUID
    currency:Currency='CNY'
    amount:str
    memo:str=Field(default='',max_length=1000)
    expected_version:int=Field(default=0,ge=0,strict=True)
    def values(self):
        try:n=minor(self.amount,self.currency)
        except ValueError:
            from ....core.database import HubError
            raise HubError('FINANCE_AMOUNT_INVALID',422) from None
        return {'local_date':self.local_date.isoformat(),'category_id':str(self.category_id),'currency':self.currency,'amount_minor':n,'memo':self.memo}

class BudgetWrite(Base):
    month:str
    category_id:UUID
    currency:Currency='CNY'
    amount:str
    expected_version:int=Field(default=0,ge=0,strict=True)
    _month=field_validator('month')(month_valid)
    def values(self):
        try:n=minor(self.amount,self.currency,True)
        except ValueError:
            from ....core.database import HubError
            raise HubError('FINANCE_AMOUNT_INVALID',422) from None
        return {'month':self.month,'category_id':str(self.category_id),'currency':self.currency,'amount_minor':n}

class ImportCategory(CategoryWrite):
    id:UUID
class ImportEntry(EntryWrite):
    id:UUID
class ImportBudget(BudgetWrite):
    id:UUID
class ImportWrite(Base):
    categories:list[ImportCategory]=Field(max_length=500)
    entries:list[ImportEntry]=Field(max_length=10000)
    budgets:list[ImportBudget]=Field(max_length=1000)
