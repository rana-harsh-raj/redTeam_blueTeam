# Example

- Create User
```
$ curl --header "authorization: basic " -XPOST https://dcs-devstack.dev.razorpay.in/v1/user -d '{"username": "example-user-1", "password": "example-pass-1", "roles": ["example-role-1"]}'
{"username":"example-user-1","roles":["example-role-1"]}
```

- Use example client
```
go run main.go
```
