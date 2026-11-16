"""Meal plans never mutate confirmed purchases or real stock."""
from datetime import date, timedelta
from uuid import uuid4
from decimal import Decimal
from ....core.database import connection, HubError, utc_now
from ....core.operations import activity, record, replay, request_hash
from ...knowledge.service import rows, visible, versioned, bump, create_resource
from .schemas import FACTORS, amount, milli, monday


def checked_week(day):
    if day>date(9999,12,24):
        raise HubError('DIET_DATE_RANGE',422)
    try:
        return monday(day)
    except ValueError:
        raise HubError('DIET_MONDAY_REQUIRED', 422) from None


class DietService:
    def __init__(self, database):
        self.database = database

    def _ingredient(self, c, iid):
        found=rows(c, 'SELECT * FROM diet_ingredients WHERE id=?', (iid,))
        if not found:
            raise HubError('INGREDIENT_NOT_FOUND', 404)
        return found[0]

    def ingredients(self, q='', limit=50, offset=0):
        with connection(self.database) as c:
            args=(q,)
            where='instr(lower(name),lower(?))>0'
            return {'items':rows(c,'SELECT * FROM diet_ingredients WHERE '+where+' ORDER BY name,id LIMIT ? OFFSET ?',args+(limit,offset)),
                    'total':c.exec_driver_sql('SELECT count(*) FROM diet_ingredients WHERE '+where,args).scalar_one(), 'limit':limit,'offset':offset}

    def ingredient_save(self, p, key, iid=None):
        data=p.model_dump(mode='json'); op='diet.ingredient:'+str(iid)
        with connection(self.database, write=True) as c:
            prior=replay(c,op,key,data)
            if prior:
                return self._ingredient(c,prior['id'])
            normal=p.name.casefold()
            existing=c.exec_driver_sql('SELECT id FROM diet_ingredients WHERE normalized_name=?',(normal,)).first()
            if existing and existing[0]!=iid:
                raise HubError('INGREDIENT_NAME_EXISTS',409)
            now=utc_now()
            if iid:
                if self._ingredient(c,iid)['version']!=p.expected_version:
                    raise HubError('VERSION_CONFLICT')
                c.exec_driver_sql('UPDATE diet_ingredients SET name=?,normalized_name=?,updated_at=?,version=version+1 WHERE id=?',(p.name,normal,now,iid))
            else:
                if p.expected_version!=0:
                    raise HubError('VERSION_CONFLICT')
                if c.exec_driver_sql('SELECT count(*) FROM diet_ingredients').scalar_one()>=10000:
                    raise HubError('DIET_RECORD_LIMIT',422)
                iid=str(uuid4())
                c.exec_driver_sql('INSERT INTO diet_ingredients(id,name,normalized_name,created_at,updated_at) VALUES(?,?,?,?,?)',(iid,p.name,normal,now,now))
            record(c,op,key,data,{'id':iid})
            return self._ingredient(c,iid)

    def _recipe(self, c, rid, trash=False):
        r=visible(c,rid,trash=trash)
        if r['kind']!='recipe':
            raise HubError('RECIPE_NOT_FOUND',404)
        r.update(rows(c,'SELECT * FROM recipes WHERE resource_id=?',(rid,))[0]);r.pop('resource_id')
        r['ingredients']=rows(c,'SELECT i.id,i.ingredient_id,d.name,i.quantity_milli,i.unit,i.position FROM recipe_ingredients i JOIN diet_ingredients d ON d.id=i.ingredient_id WHERE recipe_id=? ORDER BY i.position,i.id',(rid,))
        for item in r['ingredients']:
            item['quantity']=amount(item.pop('quantity_milli'))
        return r

    def recipe(self, rid):
        with connection(self.database) as c:
            return self._recipe(c,rid)

    def recipes(self,q='',favorite=False,trash=False,limit=30,offset=0):
        with connection(self.database) as c:
            where="r.privacy_level='personal' AND r.deleted_at IS "+('NOT NULL' if trash else 'NULL')+" AND instr(lower(r.title),lower(?))>0 AND (?=0 OR p.favorite=1)"
            args=(q,int(favorite))
            total=c.exec_driver_sql('SELECT count(*) FROM recipes p JOIN resources r ON r.id=p.resource_id WHERE '+where,args).scalar_one()
            ids=rows(c,'SELECT r.id FROM recipes p JOIN resources r ON r.id=p.resource_id WHERE '+where+' ORDER BY p.favorite DESC,r.updated_at DESC,r.id LIMIT ? OFFSET ?',args+(limit,offset))
            return {'items':[self._recipe(c,r['id'],trash) for r in ids],'total':total,'limit':limit,'offset':offset}

    def recipe_save(self,p,key,rid=None):
        data=p.model_dump(mode='json');op='diet.recipe:'+str(rid)
        with connection(self.database,write=True) as c:
            if rid:
                self._recipe(c,rid)
            prior=replay(c,op,key,data)
            if prior:
                return self._recipe(c,prior['id'])
            for item in p.ingredients:
                self._ingredient(c,str(item.ingredient_id))
            if rid:
                versioned(c,rid,p.expected_version)
                c.exec_driver_sql('UPDATE resources SET title=? WHERE id=?',(p.title,rid))
                c.exec_driver_sql('UPDATE recipes SET instructions=?,base_servings=?,favorite=? WHERE resource_id=?',(p.instructions,p.base_servings,int(p.favorite),rid))
                c.exec_driver_sql('DELETE FROM recipe_ingredients WHERE recipe_id=?',(rid,))
                bump(c,rid)
            else:
                if p.expected_version!=0:
                    raise HubError('VERSION_CONFLICT')
                rid=create_resource(c,'recipe',p.title)
                c.exec_driver_sql('INSERT INTO recipes VALUES(?,?,?,?)',(rid,p.instructions,p.base_servings,int(p.favorite)))
            for pos,item in enumerate(p.ingredients):
                c.exec_driver_sql('INSERT INTO recipe_ingredients VALUES(?,?,?,?,?,?,?)',(str(uuid4()),rid,str(item.ingredient_id),milli(item.quantity),item.unit,pos,utc_now()))
            activity(c,rid,'saved',['recipe']);record(c,op,key,data,{'id':rid})
            return self._recipe(c,rid)

    def recipe_trash(self,rid,p,key,restore=False):
        data=p.model_dump(mode='json');op='diet.recipe.state:'+rid+':'+str(restore)
        with connection(self.database,write=True) as c:
            r=self._recipe(c,rid,True)
            if replay(c,op,key,data) is None:
                versioned(c,rid,p.expected_version,trash=True)
                if bool(r['deleted_at'])!=restore:
                    raise HubError('DIET_STATE_CONFLICT')
                c.exec_driver_sql('UPDATE resources SET deleted_at=? WHERE id=?',(None if restore else utc_now(),rid))
                bump(c,rid);activity(c,rid,'restored' if restore else 'trashed',['deleted_at']);record(c,op,key,data,{'id':rid})
            return self._recipe(c,rid,True)

    def _dishes(self,c,meal_ids):
        """Dish rows for many meals at once, with resolved recipe titles."""
        if not meal_ids:
            return {}
        marks=','.join('?' for _ in meal_ids)
        found=rows(c,f"""SELECT d.id,d.meal_id,d.recipe_id,d.label,d.position,
                 r.title AS recipe_title,r.version AS recipe_version
          FROM meal_dishes d LEFT JOIN resources r
            ON r.id=d.recipe_id AND r.privacy_level='personal' AND r.deleted_at IS NULL
          WHERE d.meal_id IN ({marks}) ORDER BY d.meal_id,d.position,d.id""",tuple(meal_ids))
        grouped={}
        for row in found:
            # A recipe the user trashed is still referenced; keep the meal readable
            # but say the name is unavailable rather than inventing one.
            name=row['recipe_title'] if row['recipe_title'] else ('（食谱已删除）' if row['recipe_id'] else row['label'])
            grouped.setdefault(row['meal_id'],[]).append({
                'id':row['id'],'recipe_id':row['recipe_id'],'label':row['label'],
                'name':name,'recipe_version':row['recipe_version'],'position':row['position'],
            })
        return grouped

    def _meal(self,c,mid,trash=False):
        found=rows(c,'SELECT * FROM meal_plans WHERE id=?'+('' if trash else ' AND deleted_at IS NULL'),(mid,))
        if not found:
            raise HubError('MEAL_NOT_FOUND',404)
        r=found[0];dishes=self._dishes(c,[mid]).get(mid,[])
        legacy=rows(c,"SELECT title,version FROM resources WHERE id=? AND privacy_level='personal' AND deleted_at IS NULL",(r['recipe_id'],)) if r['recipe_id'] else []
        legacy=legacy[0] if legacy else None
        if not dishes and r['recipe_id'] and not legacy:
            raise HubError('RESOURCE_NOT_FOUND',404)   # its only recipe is private or trashed
        if not dishes and legacy:
            dishes=[{'id':mid,'recipe_id':r['recipe_id'],'label':'','name':legacy['title'],
                     'recipe_version':legacy['version'],'position':0}]
        # A meal that only references a private or trashed recipe is not operable:
        # confirming it exists would reveal a hidden identity. Free-text dishes
        # carry no such reference, so they remain visible.
        if dishes and not any(d['recipe_id'] is None or d['recipe_version'] is not None for d in dishes):
            raise HubError('RESOURCE_NOT_FOUND',404)
        return {**r,'dishes':dishes,
                'title':dishes[0]['name'] if dishes else (legacy['title'] if legacy else ''),
                'recipe_version':legacy['version'] if legacy else None}

    def _meals(self,c,start,end):
        found=rows(c,"""SELECT m.* FROM meal_plans m
          WHERE m.local_date>=? AND m.local_date<? AND m.deleted_at IS NULL
          ORDER BY m.local_date,CASE m.slot WHEN 'breakfast' THEN 0 WHEN 'lunch' THEN 1 ELSE 2 END,m.created_at,m.id""",(start,end))
        grouped=self._dishes(c,[m['id'] for m in found])
        result=[]
        for meal in found:
            dishes=grouped.get(meal['id'],[])
            if not dishes and meal['recipe_id']:
                # A meal saved before dishes existed still names one recipe.
                legacy_row=rows(c,"SELECT title,version FROM resources WHERE id=? AND privacy_level='personal' AND deleted_at IS NULL",(meal['recipe_id'],))
                if not legacy_row:
                    # The only recipe this meal names is private or trashed: hide the meal, as for new-style meals.
                    continue
                dishes=[{'id':meal['id'],'recipe_id':meal['recipe_id'],'label':'',
                         'name':legacy_row[0]['title'],'recipe_version':legacy_row[0]['version'],'position':0}]
                grouped[meal['id']]=dishes
            # A trashed recipe hides the whole meal, matching the previous visibility rule.
            visible=[d for d in dishes if d['recipe_id'] is None or d['recipe_version'] is not None]
            if dishes and not visible:
                continue
            legacy=rows(c,"SELECT title,version FROM resources WHERE id=? AND privacy_level='personal' AND deleted_at IS NULL",(meal['recipe_id'],)) if meal['recipe_id'] else []
            legacy=legacy[0] if legacy else None
            result.append({**meal,'dishes':visible,
                           'title':visible[0]['name'] if visible else (legacy['title'] if legacy else ''),
                           'recipe_version':legacy['version'] if legacy else None})
        return result

    def meals(self,week):
        start=checked_week(week);end=(week+timedelta(days=7)).isoformat()
        with connection(self.database) as c:
            return {'week_start':start,'items':self._meals(c,start,end)}

    def today(self,day):
        if day==date.max:
            raise HubError('DIET_DATE_RANGE',422)
        with connection(self.database) as c:
            return {'local_date':day.isoformat(),'items':self._meals(c,day.isoformat(),(day+timedelta(days=1)).isoformat())}

    def meal_save(self,p,key,mid=None):
        data=p.model_dump(mode='json');op='diet.meal:'+str(mid)
        with connection(self.database,write=True) as c:
            if mid:
                self._meal(c,mid)
            prior=replay(c,op,key,data)
            if prior:
                return self._meal(c,prior['id'],True)
            for dish in p.dishes:
                if dish.recipe_id is not None:
                    self._recipe(c,str(dish.recipe_id))
            now=utc_now()
            if mid:
                if self._meal(c,mid)['version']!=p.expected_version:
                    raise HubError('VERSION_CONFLICT')
                c.exec_driver_sql('UPDATE meal_plans SET local_date=?,slot=?,recipe_id=?,servings=?,note=?,version=version+1,updated_at=? WHERE id=?',
                    (data['local_date'],p.slot,str(p.recipe_id) if p.recipe_id else None,p.servings,p.note,now,mid))
                c.exec_driver_sql('DELETE FROM meal_dishes WHERE meal_id=?',(mid,))
            else:
                if p.expected_version!=0:
                    raise HubError('VERSION_CONFLICT')
                if c.exec_driver_sql('SELECT count(*) FROM meal_plans WHERE local_date=? AND deleted_at IS NULL',(data['local_date'],)).scalar_one()>=30:
                    raise HubError('DIET_MEAL_LIMIT',422)
                mid=str(uuid4())
                c.exec_driver_sql('INSERT INTO meal_plans(id,local_date,slot,recipe_id,servings,note,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                    (mid,data['local_date'],p.slot,str(p.recipe_id) if p.recipe_id else None,p.servings,p.note,now,now))
            dishes=list(p.dishes) or ([type('D',(),{'recipe_id':p.recipe_id,'label':''})()] if p.recipe_id else [])
            for position,dish in enumerate(dishes):
                c.exec_driver_sql('INSERT INTO meal_dishes(id,meal_id,recipe_id,label,position) VALUES(?,?,?,?,?)',
                    (str(uuid4()),mid,str(dish.recipe_id) if dish.recipe_id else None,dish.label,position))
            record(c,op,key,data,{'id':mid})
            return self._meal(c,mid)

    def meal_remove(self,mid,p,key):
        data=p.model_dump(mode='json');op='diet.meal.remove:'+mid
        with connection(self.database,write=True) as c:
            r=self._meal(c,mid,True)
            if replay(c,op,key,data) is None:
                if r['version']!=p.expected_version or r['deleted_at']:
                    raise HubError('VERSION_CONFLICT')
                c.exec_driver_sql('UPDATE meal_plans SET deleted_at=?,updated_at=?,version=version+1 WHERE id=?',(utc_now(),utc_now(),mid))
                record(c,op,key,data,{'id':mid})
            return self._meal(c,mid,True)

    def _list(self,c,week):
        found=rows(c,'SELECT * FROM shopping_lists WHERE week_start=?',(week,))
        return found[0] if found else {'id':None,'week_start':week,'version':0,'created_at':None,'updated_at':None}

    def _shopping(self,c,week):
        header=self._list(c,week)
        items=rows(c,'SELECT s.*,d.name FROM shopping_items s JOIN diet_ingredients d ON d.id=s.ingredient_id WHERE list_id=? ORDER BY s.created_at,s.id',(header['id'],))
        for r in items:
            r['quantity']=amount(r.pop('quantity_milli'))
        return {**header,'items':items}

    def shopping(self,week):
        with connection(self.database) as c:
            return self._shopping(c,checked_week(week))

    def _touch_list(self,c,week,version):
        r=self._list(c,week)
        if r['version']!=version:
            raise HubError('VERSION_CONFLICT')
        now=utc_now()
        if r['id']:
            c.exec_driver_sql('UPDATE shopping_lists SET version=version+1,updated_at=? WHERE id=?',(now,r['id']))
        else:
            r['id']=str(uuid4())
            c.exec_driver_sql('INSERT INTO shopping_lists(id,week_start,created_at,updated_at) VALUES(?,?,?,?)',(r['id'],week,now,now))
        return r['id']

    def _insert_shop(self,c,lid,iid,quantity,unit,note,purchased,origin,digest=None):
        if quantity<=0 or quantity>1_000_000_000_000:
            raise HubError('DIET_QUANTITY_LIMIT',422)
        if c.exec_driver_sql('SELECT count(*) FROM shopping_items WHERE list_id=?',(lid,)).scalar_one()>=2000:
            raise HubError('DIET_RECORD_LIMIT',422)
        c.exec_driver_sql('INSERT INTO shopping_items(id,list_id,ingredient_id,quantity_milli,unit,note,purchased,origin,source_digest,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(str(uuid4()),lid,iid,quantity,unit,note,int(purchased),origin,digest,utc_now(),utc_now()))

    def shopping_save(self,week,p,key,sid=None):
        week=checked_week(week);data=p.model_dump(mode='json');op='diet.shopping:'+week+':'+str(sid)
        with connection(self.database,write=True) as c:
            if replay(c,op,key,data):
                return self._shopping(c,week)
            self._ingredient(c,str(p.ingredient_id));lid=self._touch_list(c,week,p.expected_version)
            if sid:
                if not c.exec_driver_sql('SELECT 1 FROM shopping_items WHERE id=? AND list_id=? AND removed_at IS NULL',(sid,lid)).first():
                    raise HubError('SHOPPING_ITEM_NOT_FOUND',404)
                c.exec_driver_sql('UPDATE shopping_items SET ingredient_id=?,quantity_milli=?,unit=?,note=?,purchased=?,updated_at=? WHERE id=?',(str(p.ingredient_id),milli(p.quantity,1_000_000_000_000),p.unit,p.note,int(p.purchased),utc_now(),sid))
            else:
                self._insert_shop(c,lid,str(p.ingredient_id),milli(p.quantity,1_000_000_000_000),p.unit,p.note,p.purchased,'manual')
            record(c,op,key,data,{'week':week})
            return self._shopping(c,week)

    def shopping_state(self,week,sid,p,key):
        week=checked_week(week);data=p.model_dump(mode='json');op='diet.shopping.state:'+week+':'+sid
        with connection(self.database,write=True) as c:
            if replay(c,op,key,data):
                return self._shopping(c,week)
            lid=self._touch_list(c,week,p.expected_version)
            found=rows(c,'SELECT * FROM shopping_items WHERE id=? AND list_id=?',(sid,lid))
            if not found:
                raise HubError('SHOPPING_ITEM_NOT_FOUND',404)
            if bool(found[0]['removed_at'])==p.removed:
                raise HubError('DIET_STATE_CONFLICT')
            c.exec_driver_sql('UPDATE shopping_items SET removed_at=?,updated_at=? WHERE id=?',(utc_now() if p.removed else None,utc_now(),sid))
            record(c,op,key,data,{'week':week})
            return self._shopping(c,week)

    def _suggestions(self,c,week):
        meals=self._meals(c,week,(date.fromisoformat(week)+timedelta(days=7)).isoformat())
        totals={};facts=[]
        for meal in meals:
            # Only dishes that point at a real recipe contribute ingredients.
            # A free-text dish states what will be eaten, not what must be bought.
            for dish in meal['dishes']:
                if not dish['recipe_id'] or dish['recipe_version'] is None:
                    continue
                recipe=self._recipe(c,dish['recipe_id'])
                facts.append((meal['id'],meal['version'],dish['id'],recipe['id'],recipe['version']))
                for r in recipe['ingredients']:
                    unit,factor=FACTORS[r['unit']];pair=(r['ingredient_id'],unit)
                    qty=milli(r['quantity'])*factor*meal['servings']
                    qty=(qty+recipe['base_servings']-1)//recipe['base_servings']
                    totals[pair]=totals.get(pair,0)+qty
        listing=self._shopping(c,week);confirmed={}
        for r in listing['items']:
            if r['removed_at']:
                continue
            unit,factor=FACTORS[r['unit']];pair=(r['ingredient_id'],unit)
            confirmed[pair]=confirmed.get(pair,0)+int(Decimal(r['quantity'])*1000)*factor
        if len(totals)>500:
            raise HubError('DIET_SUGGESTION_LIMIT',422)
        result=[]
        for (iid,unit),required in sorted(totals.items()):
            ingredient=self._ingredient(c,iid)
            stock=rows(c,'SELECT * FROM inventory_balances WHERE ingredient_id=? AND unit=?',(iid,unit))
            available=stock[0]['quantity_milli'] if stock else 0
            bought=confirmed.get((iid,unit),0)
            result.append({'ingredient_id':iid,'name':ingredient['name'],'unit':unit,'required':amount(required),
                'stock':amount(available),'confirmed':amount(bought),'quantity':amount(max(required-available-bought,0)),
                'ingredient_version':ingredient['version'],'stock_version':stock[0]['version'] if stock else 0})
        digest=request_hash({'week':week,'meals':facts,'items':result,'list_version':listing['version']})
        return {'week_start':week,'version':listing['version'],'digest':digest,'items':result,
                'policy':'当前库存仅供对照，不跨周预留；已买仍计入本周已确认量，勾选不入库。'}

    def suggestions(self,week):
        with connection(self.database) as c:
            return self._suggestions(c,checked_week(week))

    def confirm(self,week,p,key):
        week=checked_week(week);data=p.model_dump(mode='json');op='diet.confirm:'+week
        with connection(self.database,write=True) as c:
            # Confirmed shopping facts are independent of a subsequently hidden recipe.
            if replay(c,op,key,data):
                return self._shopping(c,week)
            current=self._suggestions(c,week)
            if current['digest']!=p.digest:
                raise HubError('DIET_PREVIEW_STALE')
            by_pair={(r['ingredient_id'],r['unit']):r for r in current['items']}
            selected=[]
            for item in p.selected:
                r=by_pair.get((str(item.ingredient_id),item.unit))
                if not r or Decimal(r['quantity'])<=0:
                    raise HubError('DIET_PREVIEW_STALE')
                selected.append(r)
            lid=self._touch_list(c,week,p.expected_version)
            for r in selected:
                self._insert_shop(c,lid,r['ingredient_id'],int(Decimal(r['quantity'])*1000),r['unit'],'',False,'menu',p.digest)
            record(c,op,key,data,{'week':week})
            return self._shopping(c,week)

    def stock(self,q='',limit=50,offset=0):
        with connection(self.database) as c:
            where='instr(lower(d.name),lower(?))>0';args=(q,)
            found=rows(c,'SELECT s.*,d.name FROM inventory_balances s JOIN diet_ingredients d ON d.id=s.ingredient_id WHERE '+where+' ORDER BY d.name,s.unit,s.id LIMIT ? OFFSET ?',args+(limit,offset))
            for r in found:
                r['quantity']=amount(r.pop('quantity_milli'))
            return {'items':found,'total':c.exec_driver_sql('SELECT count(*) FROM inventory_balances s JOIN diet_ingredients d ON d.id=s.ingredient_id WHERE '+where,args).scalar_one(),'limit':limit,'offset':offset}

    def stock_save(self,p,key):
        data=p.model_dump(mode='json');op='diet.stock'
        with connection(self.database,write=True) as c:
            self._ingredient(c,str(p.ingredient_id))
            prior=replay(c,op,key,data)
            if not prior:
                old=rows(c,'SELECT * FROM inventory_balances WHERE ingredient_id=? AND unit=?',(str(p.ingredient_id),p.unit))
                if (old[0]['version'] if old else 0)!=p.expected_version:
                    raise HubError('VERSION_CONFLICT')
                now=utc_now();sid=old[0]['id'] if old else str(uuid4())
                if old:
                    c.exec_driver_sql('UPDATE inventory_balances SET quantity_milli=?,version=version+1,updated_at=? WHERE id=?',(milli(p.quantity),now,sid))
                else:
                    c.exec_driver_sql('INSERT INTO inventory_balances(id,ingredient_id,unit,quantity_milli,created_at,updated_at) VALUES(?,?,?,?,?,?)',(sid,str(p.ingredient_id),p.unit,milli(p.quantity),now,now))
                record(c,op,key,data,{'id':sid})
            else:
                sid=prior['id']
            found=rows(c,'SELECT * FROM inventory_balances WHERE id=?',(sid,))
            if not found:
                # The save succeeded earlier and the row was removed afterwards: say so instead of failing.
                return {'id':sid,'status':'removed'}
            r=found[0]
            r['quantity']=amount(r.pop('quantity_milli'));return r

    def stock_remove(self,sid,p,key):
        """Physical removal of one balance row. No other table references it and
        the quantity is a snapshot, not a derived total, so deleting loses nothing
        another record depends on."""
        data=p.model_dump(mode='json');op='diet.stock.remove:'+sid
        with connection(self.database,write=True) as c:
            prior=replay(c,op,key,data)
            if prior:
                return {'status':'removed','id':sid}
            old=rows(c,'SELECT * FROM inventory_balances WHERE id=?',(sid,))
            if not old:
                raise HubError('STOCK_NOT_FOUND',404)
            if old[0]['version']!=p.expected_version:
                raise HubError('VERSION_CONFLICT')
            c.exec_driver_sql('DELETE FROM inventory_balances WHERE id=? AND version=?',(sid,p.expected_version))
            record(c,op,key,data,{'id':sid})
            return {'status':'removed','id':sid}
