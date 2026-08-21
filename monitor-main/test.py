import baostock as bs
lg = bs.login()  # 匿名登录，无需账号密码
rs = bs.query_history_k_data_plus(
    "sz.127040",
    "date,code,close",
    start_date="2026-01-01",
    frequency="d",
)
print(rs.get_data())
bs.logout()