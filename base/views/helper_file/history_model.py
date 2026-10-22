import pandas
from sklearn import linear_model
from datetime import datetime
from base.models import *


def history_model(user_date, user_product_id):
   import datetime as dt
    
   d = []
   p = []
   
   products = ProductPriceHistory.objects.filter(product=user_product_id)
   for i in products:
       d.append(i.createdAt)
       p.append(i.price)
       
   data = {'date': d,
           'price': p}

   df = pandas.DataFrame.from_dict(data)

   df['date'] = pandas.to_datetime(df['date'])
   df['date'] = df['date'].map(dt.datetime.toordinal)
   #print(df['date'])
   #print("***************************")
   #print(df['price'])

   X = df[['date']]
   y = df[['price']]

   regr = linear_model.LinearRegression()
   regr.fit(X, y)

   date_str = user_date
   ate_object = datetime.strptime(date_str, '%Y-%m-%d').date()

   #print('Date:', date_time_obj.date())
   #print('Time:', date_time_obj.time())
   #print('Date-time:', date_time_obj)
   dt = ate_object.toordinal()


   predicted = regr.predict([[dt]])

   return predicted
